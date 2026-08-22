import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const fencePath = "dist/direct-financial-fence-v1.js";
const socketControlPath = "dist/direct-socket-control-v1.js";
const pipPath = "dist/direct-pip-precision-v1.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`deriv-websocket-transport missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`deriv-websocket-transport ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

// ---------------------------------------------------------------------------
// Current Deriv Options architecture:
// - public WSS is valid for unauthenticated market data;
// - authenticated demo/real WSS (OTP URL) can carry market data AND trading;
// - Deriv recommends { ping: 1 } every 30–60s to keep sockets alive.
//
// Once the authenticated account socket is OPEN, make it the one canonical market
// transport as well as the trading transport. The public socket remains a bounded
// fallback only while the private/account socket is unavailable. This removes the
// public socket as a hard single point of failure and prevents duplicate tick/history
// subscriptions from leaking across reconnects.
// ---------------------------------------------------------------------------
let engine = read(enginePath);

if (!engine.includes("marketDataKind: \"\",")) {
  engine = replaceOne(
    engine,
    "    privateWs: null,\n",
    "    privateWs: null,\n    marketDataKind: \"\",\n",
    "market data authority state",
  );
}

engine = replaceOne(
  engine,
  "    if (!state.running || state.publicReconnectTimer) return;",
  "    if (!state.running || state.publicReconnectTimer || state.marketDataKind === \"private\") return;",
  "stop public retry while authenticated socket owns market data",
);

engine = replaceOne(
  engine,
  "      if (!state.running || !browserNetworkOnline()) {\n        if (!browserNetworkOnline()) markPublicOffline();\n        return;\n      }",
  "      if (!state.running || state.marketDataKind === \"private\" || !browserNetworkOnline()) {\n        if (!browserNetworkOnline()) markPublicOffline();\n        return;\n      }",
  "cancel queued public reconnect after private promotion",
);

engine = replaceOne(
  engine,
  "  function connectPublic() {\n    if (state.publicWs?.readyState === WebSocket.OPEN) return Promise.resolve(state.publicWs);\n    if (state.publicConnectPromise) return state.publicConnectPromise;",
  "  function connectPublic() {\n    if (state.marketDataKind === \"private\" && state.privateWs?.readyState === WebSocket.OPEN) return Promise.resolve(null);\n    if (state.publicWs?.readyState === WebSocket.OPEN) return Promise.resolve(state.publicWs);\n    if (state.publicConnectPromise) return state.publicConnectPromise;",
  "do not construct a second public socket while private is canonical",
);

engine = replaceOne(
  engine,
  "    if (kind === \"public\" && payload.tick) {",
  "    if ((kind === \"public\" || kind === \"private\") && payload.tick) {",
  "accept market ticks on authenticated Options socket",
);
engine = replaceOne(
  engine,
  "    if (kind === \"public\" && payload.history && payload.echo_req?.ticks_history) {",
  "    if ((kind === \"public\" || kind === \"private\") && payload.history && payload.echo_req?.ticks_history) {",
  "accept tick history on authenticated Options socket",
);
engine = replaceOne(
  engine,
  "      sendNoWait(\"public\", { ticks: symbol, subscribe: 1 });",
  "      sendNoWait(kind, { ticks: symbol, subscribe: 1 });",
  "continue live ticks on the history response transport",
);

const oldSubscribe = `  function subscribeMarkets() {\n    if (!state.running || !state.strategy || state.publicWs?.readyState !== WebSocket.OPEN) return;\n    for (const symbol of state.strategy.markets) {\n      if (state.subscribedMarkets.has(symbol)) continue;\n      if (sendNoWait("public", { ticks: symbol, subscribe: 1 })) state.subscribedMarkets.add(symbol);\n    }\n  }`;
const newSubscribe = `  function currentMarketDataKind() {\n    if (state.armed && !state.ownerLost && state.privateWs?.readyState === WebSocket.OPEN) return "private";\n    if (state.publicWs?.readyState === WebSocket.OPEN) return "public";\n    return "";\n  }\n\n  function subscribeMarkets() {\n    if (!state.running || !state.strategy) return;\n    const kind = currentMarketDataKind();\n    if (!kind) return;\n    if (state.marketDataKind !== kind) {\n      state.marketDataKind = kind;\n      state.subscribedMarkets.clear();\n    }\n    for (const symbol of state.strategy.markets) {\n      if (state.subscribedMarkets.has(symbol)) continue;\n      if (sendNoWait(kind, { ticks: symbol, subscribe: 1 })) state.subscribedMarkets.add(symbol);\n    }\n  }`;
engine = replaceOne(engine, oldSubscribe, newSubscribe, "canonical market subscription transport");

const credentialMarker = "  function clearDirectBrowserCredential() {";
if (!engine.includes("function promotePrivateMarketTransport(")) {
  const helpers = `  function promotePrivateMarketTransport(ws) {\n    if (!state.running || !state.armed || state.ownerLost) return;\n    if (!ws || state.privateWs !== ws || ws.readyState !== WebSocket.OPEN) return;\n    const switched = state.marketDataKind !== "private";\n    state.marketDataKind = "private";\n    if (switched) state.subscribedMarkets.clear();\n    try { clearPublicReconnectTimer(); } catch (_) {}\n\n    const publicWs = state.publicWs;\n    if (publicWs) {\n      // Invalidate the public socket generation BEFORE closing it. Its stale close\n      // handler is then unable to clear/replace the newer canonical transport.\n      state.publicGeneration += 1;\n      state.publicWs = null;\n      state.publicConnectPromise = null;\n      try { publicWs.close(1000, "authenticated Options market transport active"); } catch (_) {}\n    }\n    subscribeMarkets();\n  }\n\n  function fallbackToPublicMarketTransport() {\n    if (!state.running) return;\n    if (state.marketDataKind === "private") {\n      state.marketDataKind = "";\n      state.subscribedMarkets.clear();\n    }\n    if (browserNetworkOnline()) connectPublic().then(() => subscribeMarkets()).catch(() => {});\n  }\n\n`;
  const at = engine.indexOf(credentialMarker);
  if (at < 0) throw new Error("deriv-websocket-transport OAuth credential marker missing");
  engine = engine.slice(0, at) + helpers + engine.slice(at);
}

engine = replaceOne(
  engine,
  "          state.privateLastCloseCode = 0;\n          if (state.running && !state.ownerLost) {",
  "          state.privateLastCloseCode = 0;\n          promotePrivateMarketTransport(ws);\n          if (state.running && !state.ownerLost) {",
  "promote authenticated socket on open",
);

engine = replaceOne(
  engine,
  "          state.privateConnectPromise = null;\n          const code = Number(event?.code || 0);",
  "          state.privateConnectPromise = null;\n          fallbackToPublicMarketTransport();\n          const code = Number(event?.code || 0);",
  "fall back to public market data during private reconnect",
);

engine = replaceOne(
  engine,
  "        private_ready: state.privateWs?.readyState === WebSocket.OPEN,",
  "        private_ready: state.privateWs?.readyState === WebSocket.OPEN,\n        market_data_ready: Boolean(\n          (state.marketDataKind === \"private\" && state.privateWs?.readyState === WebSocket.OPEN)\n          || (state.marketDataKind !== \"private\" && state.publicWs?.readyState === WebSocket.OPEN)\n        ),\n        market_data_kind: currentMarketDataKind(),",
  "export canonical market transport diagnostics",
);

engine = replaceOne(
  engine,
  "    if (diagnostics.public_ready !== true) {",
  "    if (diagnostics.market_data_ready !== true) {",
  "do not hard-block execution on public socket when authenticated market transport is ready",
);
engine = engine.replace(
  "Auto Trading is ON; public Deriv market-data WebSocket is reconnecting",
  "Auto Trading is ON; Deriv market-data transport is reconnecting",
);

// ---------------------------------------------------------------------------
// The history/BUS fence originally recognized only the public URL as market data.
// Current Deriv authenticated Options sockets also support active_symbols, history,
// and ticks. Apply the exact same mandatory history hydration to either transport.
// ---------------------------------------------------------------------------
let fence = read(fencePath);
if (!fence.includes("const marketDataSocket = publicSocket || authenticatedOptionsSocket;")) {
  fence = replaceOne(
    fence,
    "    const authenticatedOptionsSocket = /\\/trading\\/v1\\/options\\/ws\\/(demo|real)(?:\\?|$)/.test(urlText);\n",
    "    const authenticatedOptionsSocket = /\\/trading\\/v1\\/options\\/ws\\/(demo|real)(?:\\?|$)/.test(urlText);\n    const marketDataSocket = publicSocket || authenticatedOptionsSocket;\n",
    "Options market data socket classification",
  );
}
fence = replaceOne(
  fence,
  "      if (!publicSocket || message?.msg_type !== \"tick\" || !message?.tick) return;",
  "      if (!marketDataSocket || message?.msg_type !== \"tick\" || !message?.tick) return;",
  "market tick event on either Options transport",
);
fence = replaceOne(
  fence,
  "      if (!publicSocket) return;",
  "      if (!marketDataSocket) return;",
  "history response on either Options transport",
);
fence = replaceOne(
  fence,
  "    if (publicSocket) {\n      socket.addEventListener(\"close\", () => {",
  "    if (marketDataSocket) {\n      socket.addEventListener(\"close\", () => {",
  "cleanup history requests for either transport",
);
fence = replaceOne(
  fence,
  "      if (publicSocket && payload?.ticks && Number(payload?.subscribe || 0) === 1) {",
  "      if (marketDataSocket && payload?.ticks && Number(payload?.subscribe || 0) === 1) {",
  "mandatory history preload on authenticated transport",
);

// Socket keepalive must cover public + demo + real and must retain one timer/socket.
const socketControl = read(socketControlPath);
for (const required of [
  "optionsSocket = /\\/trading\\/v1\\/options\\/ws\\/(public|demo|real)",
  "stopKeepalive(socket);\n    sendKeepalive(socket);",
  "KEEPALIVE_MS = 30000",
  "socket.addEventListener(\"close\"",
]) {
  if (!socketControl.includes(required)) throw new Error(`deriv-websocket-transport socket-control invariant missing: ${required}`);
}

// Current active_symbols schema renamed symbol -> underlying_symbol and pip ->
// pip_size. The precision wrapper must support those fields on all Options sockets.
const pip = read(pipPath);
for (const required of [
  "underlying_symbol || item?.symbol",
  "item?.pip_size ?? item?.pip",
  "options\\/ws\\/(public|demo|real)",
]) {
  if (!pip.includes(required)) throw new Error(`deriv-websocket-transport pip invariant missing: ${required}`);
}

for (const required of [
  "marketDataKind: \"\"",
  "function currentMarketDataKind()",
  "function promotePrivateMarketTransport(ws)",
  "function fallbackToPublicMarketTransport()",
  "sendNoWait(kind, { ticks: symbol, subscribe: 1 })",
  "market_data_ready:",
  "market_data_kind:",
  "state.marketDataKind === \"private\"",
]) {
  if (!engine.includes(required)) throw new Error(`deriv-websocket-transport engine invariant missing: ${required}`);
}
for (const required of [
  "const marketDataSocket = publicSocket || authenticatedOptionsSocket;",
  "marketDataSocket && payload?.ticks",
  "if (!marketDataSocket) return;",
]) {
  if (!fence.includes(required)) throw new Error(`deriv-websocket-transport fence invariant missing: ${required}`);
}

write(enginePath, engine);
write(fencePath, fence);

let index = read(indexPath);
index = index.replace(/\/deriv-direct-execution-v2\.js\?v=[^"']+/g, "/deriv-direct-execution-v2.js?v=20260822-single-options-transport-v1");
index = index.replace(/\/direct-financial-fence-v1\.js\?v=[^"']+/g, "/direct-financial-fence-v1.js?v=20260822-auth-market-history-v1");
index = index.replace(/\/direct-socket-control-v1\.js\?v=[^"']+/g, "/direct-socket-control-v1.js?v=20260822-all-options-ping-v1");
index = index.replace(/\/direct-pip-precision-v1\.js\?v=[^"']+/g, "/direct-pip-precision-v1.js?v=20260822-current-schema-v1");
for (const required of [
  "/deriv-direct-execution-v2.js?v=20260822-single-options-transport-v1",
  "/direct-financial-fence-v1.js?v=20260822-auth-market-history-v1",
  "/direct-socket-control-v1.js?v=20260822-all-options-ping-v1",
  "/direct-pip-precision-v1.js?v=20260822-current-schema-v1",
]) {
  if (!index.includes(required)) throw new Error(`deriv-websocket-transport cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("DERIV_WEBSOCKET_TRANSPORT_V1_INSTALLED authenticated_market_transport=true public_fallback_only=true duplicate_subscriptions_prevented=true public_private_ping_30s=true history_on_both=true current_active_symbols_schema=true");
