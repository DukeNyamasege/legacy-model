import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const fencePath = "dist/direct-financial-fence-v1.js";
const socketControlPath = "dist/direct-socket-control-v1.js";
const pipPath = "dist/direct-pip-precision-v1.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`fetch-timeout-helper missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`fetch-timeout-helper ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

let engine = read(enginePath);

// The source execution engine defines fetchWithTimeout() between connectPublic()
// and connectPrivate(). Later public-transport finalizers replace that region and
// can accidentally remove the helper while leaving Start/OAuth code calling it.
// Restore the exact bounded same-origin helper at the final production boundary.
const helper = `  async function fetchWithTimeout(url, options, timeoutMs) {\n    const controller = new AbortController();\n    const timer = setTimeout(() => controller.abort(), timeoutMs);\n    try {\n      return await originalFetch(url, { credentials: "include", cache: "no-store", ...options, signal: controller.signal });\n    } finally {\n      clearTimeout(timer);\n    }\n  }\n\n`;

const helperMarker = "  async function fetchWithTimeout(";
const credentialMarker = "  function clearDirectBrowserCredential() {";

if (!engine.includes(helperMarker)) {
  const markerIndex = engine.indexOf(credentialMarker);
  if (markerIndex < 0) {
    throw new Error("fetch-timeout-helper cannot restore helper: OAuth credential boundary missing");
  }
  engine = engine.slice(0, markerIndex) + helper + engine.slice(markerIndex);
}

const helperCount = engine.split(helperMarker).length - 1;
if (helperCount !== 1) {
  throw new Error(`fetch-timeout-helper expected exactly one helper definition, got ${helperCount}`);
}

for (const required of [
  "async function fetchWithTimeout(url, options, timeoutMs)",
  'apiPath("/me/direct-execution/bootstrap")',
  'apiPath("/me/direct-execution/arm")',
  'apiPath("/me/runtime-sync")',
  "response = await fetchWithTimeout(",
  "const response = await fetchWithTimeout(",
]) {
  if (!engine.includes(required)) {
    throw new Error(`fetch-timeout-helper final engine invariant missing: ${required}`);
  }
}
if (!engine.includes('const originalFetch = window.fetch.bind(window);')) {
  throw new Error("fetch-timeout-helper originalFetch authority missing");
}

// ---------------------------------------------------------------------------
// Deriv's current Options API documents three WebSocket endpoints:
// public (market data), demo (OTP-authenticated), and real (OTP-authenticated).
// Authenticated Options sockets can carry market data and trading operations.
// Make the authenticated account socket the canonical market-data transport once
// Start is armed; keep public WSS only as a temporary fallback. This prevents a
// public socket outage from blocking trading and prevents duplicate subscriptions
// from accumulating across reconnects.
// ---------------------------------------------------------------------------
if (!engine.includes('marketDataKind: "",')) {
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
  "stop public reconnect while private market transport is canonical",
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
  "avoid second public socket while authenticated market transport is active",
);

const oldSubscribe = `  function subscribeMarkets() {\n    if (!state.running || !state.strategy || state.publicWs?.readyState !== WebSocket.OPEN) return;\n    for (const symbol of state.strategy.markets) {\n      if (state.subscribedMarkets.has(symbol)) continue;\n      if (sendNoWait("public", { ticks: symbol, subscribe: 1 })) state.subscribedMarkets.add(symbol);\n    }\n  }`;
const newSubscribe = `  function currentMarketDataKind() {\n    if (state.armed && !state.ownerLost && state.privateWs?.readyState === WebSocket.OPEN) return "private";\n    if (state.publicWs?.readyState === WebSocket.OPEN) return "public";\n    return "";\n  }\n\n  function subscribeMarkets() {\n    if (!state.running || !state.strategy) return;\n    const kind = currentMarketDataKind();\n    if (!kind) return;\n    if (state.marketDataKind !== kind) {\n      state.marketDataKind = kind;\n      state.subscribedMarkets.clear();\n    }\n    for (const symbol of state.strategy.markets) {\n      if (state.subscribedMarkets.has(symbol)) continue;\n      if (sendNoWait(kind, { ticks: symbol, subscribe: 1 })) state.subscribedMarkets.add(symbol);\n    }\n  }`;
engine = replaceOne(engine, oldSubscribe, newSubscribe, "canonical market subscription transport");

engine = replaceOne(
  engine,
  '    if (kind === "public" && payload.tick) {',
  '    if ((kind === "public" || kind === "private") && payload.tick) {',
  "accept market ticks on authenticated Options socket",
);
engine = replaceOne(
  engine,
  '    if (kind === "public" && payload.history && payload.echo_req?.ticks_history) {',
  '    if ((kind === "public" || kind === "private") && payload.history && payload.echo_req?.ticks_history) {',
  "accept history on authenticated Options socket",
);
engine = replaceOne(
  engine,
  '      sendNoWait("public", { ticks: symbol, subscribe: 1 });',
  '      sendNoWait(kind, { ticks: symbol, subscribe: 1 });',
  "continue live ticks on the response transport",
);

if (!engine.includes("function promotePrivateMarketTransport(")) {
  const transportHelpers = `  function promotePrivateMarketTransport(ws) {\n    if (!state.running || !state.armed || state.ownerLost) return;\n    if (!ws || state.privateWs !== ws || ws.readyState !== WebSocket.OPEN) return;\n    const switched = state.marketDataKind !== "private";\n    state.marketDataKind = "private";\n    if (switched) state.subscribedMarkets.clear();\n    try { clearPublicReconnectTimer(); } catch (_) {}\n\n    const publicWs = state.publicWs;\n    if (publicWs) {\n      state.publicGeneration += 1;\n      state.publicWs = null;\n      state.publicConnectPromise = null;\n      try { publicWs.close(1000, "authenticated Options market transport active"); } catch (_) {}\n    }\n    subscribeMarkets();\n  }\n\n  function fallbackToPublicMarketTransport() {\n    if (!state.running) return;\n    if (state.marketDataKind === "private") {\n      state.marketDataKind = "";\n      state.subscribedMarkets.clear();\n    }\n    if (browserNetworkOnline()) connectPublic().then(() => subscribeMarkets()).catch(() => {});\n  }\n\n`;
  const markerIndex = engine.indexOf(credentialMarker);
  if (markerIndex < 0) throw new Error("fetch-timeout-helper OAuth credential boundary missing for transport helpers");
  engine = engine.slice(0, markerIndex) + transportHelpers + engine.slice(markerIndex);
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
  "public fallback during authenticated socket reconnect",
);

engine = replaceOne(
  engine,
  "        private_ready: state.privateWs?.readyState === WebSocket.OPEN,",
  "        private_ready: state.privateWs?.readyState === WebSocket.OPEN,\n        market_data_ready: Boolean(\n          (state.marketDataKind === \"private\" && state.privateWs?.readyState === WebSocket.OPEN)\n          || (state.marketDataKind !== \"private\" && state.publicWs?.readyState === WebSocket.OPEN)\n        ),\n        market_data_kind: currentMarketDataKind(),",
  "canonical market transport diagnostics",
);
engine = replaceOne(
  engine,
  "    if (diagnostics.public_ready !== true) {",
  "    if (diagnostics.market_data_ready !== true) {",
  "remove public-only execution blocker",
);
engine = engine.replace(
  "Auto Trading is ON; public Deriv market-data WebSocket is reconnecting",
  "Auto Trading is ON; Deriv market-data transport is reconnecting",
);

// Mandatory historical hydration must apply to whichever Deriv Options socket owns
// market data. The financial BUY fence itself remains unchanged.
let fence = read(fencePath);
if (!fence.includes("const marketDataSocket = publicSocket || authenticatedOptionsSocket;")) {
  fence = replaceOne(
    fence,
    "    const authenticatedOptionsSocket = /\\/trading\\/v1\\/options\\/ws\\/(demo|real)(?:\\?|$)/.test(urlText);\n",
    "    const authenticatedOptionsSocket = /\\/trading\\/v1\\/options\\/ws\\/(demo|real)(?:\\?|$)/.test(urlText);\n    const marketDataSocket = publicSocket || authenticatedOptionsSocket;\n",
    "market data socket classification",
  );
}
fence = replaceOne(
  fence,
  '      if (!publicSocket || message?.msg_type !== "tick" || !message?.tick) return;',
  '      if (!marketDataSocket || message?.msg_type !== "tick" || !message?.tick) return;',
  "emit market ticks on either Options transport",
);
fence = replaceOne(
  fence,
  "      if (!publicSocket) return;",
  "      if (!marketDataSocket) return;",
  "consume history on either Options transport",
);
fence = replaceOne(
  fence,
  '    if (publicSocket) {\n      socket.addEventListener("close", () => {',
  '    if (marketDataSocket) {\n      socket.addEventListener("close", () => {',
  "cleanup hydration for either Options transport",
);
fence = replaceOne(
  fence,
  "      if (publicSocket && payload?.ticks && Number(payload?.subscribe || 0) === 1) {",
  fence.includes("if (marketDataSocket && payload?.ticks")
    ? "      if (marketDataSocket && payload?.ticks && Number(payload?.subscribe || 0) === 1) {"
    : "      if (marketDataSocket && payload?.ticks && Number(payload?.subscribe || 0) === 1) {",
  "hydrate history on authenticated transport",
);

const socketControl = read(socketControlPath);
for (const required of [
  "optionsSocket = /\\/trading\\/v1\\/options\\/ws\\/(public|demo|real)",
  "KEEPALIVE_MS = 30000",
  "stopKeepalive(socket);\n    sendKeepalive(socket);",
  'socket.addEventListener("close"',
]) {
  if (!socketControl.includes(required)) throw new Error(`fetch-timeout-helper socket-control invariant missing: ${required}`);
}

const pip = read(pipPath);
for (const required of [
  "underlying_symbol || item?.symbol",
  "item?.pip_size ?? item?.pip",
  "options\\/ws\\/(public|demo|real)",
]) {
  if (!pip.includes(required)) throw new Error(`fetch-timeout-helper pip invariant missing: ${required}`);
}

for (const required of [
  'marketDataKind: ""',
  "function currentMarketDataKind()",
  "function promotePrivateMarketTransport(ws)",
  "function fallbackToPublicMarketTransport()",
  "sendNoWait(kind, { ticks: symbol, subscribe: 1 })",
  "market_data_ready:",
  "market_data_kind:",
  'state.marketDataKind === "private"',
]) {
  if (!engine.includes(required)) throw new Error(`fetch-timeout-helper transport invariant missing: ${required}`);
}
for (const required of [
  "const marketDataSocket = publicSocket || authenticatedOptionsSocket;",
  "marketDataSocket && payload?.ticks",
  "if (!marketDataSocket) return;",
]) {
  if (!fence.includes(required)) throw new Error(`fetch-timeout-helper fence invariant missing: ${required}`);
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
  if (!index.includes(required)) throw new Error(`fetch-timeout-helper cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("FETCH_TIMEOUT_HELPER_V1_INSTALLED helper_defined_once=true arm_fetch_bounded=true bootstrap_fetch_bounded=true runtime_sync_bounded=true authenticated_market_transport=true public_fallback_only=true public_private_ping_30s=true duplicate_subscriptions_prevented=true current_active_symbols_schema=true finalizer_last=true");
