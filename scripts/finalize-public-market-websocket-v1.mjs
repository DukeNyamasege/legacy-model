import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const fencePath = "dist/direct-financial-fence-v1.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`public-market-websocket-v1 missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) {
    throw new Error(`public-market-websocket-v1 ${label}: expected 1 source match or installed shape, got ${count}`);
  }
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`public-market-websocket-v1 ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

// ---------------------------------------------------------------------------
// 1. Public market-data WebSocket lifecycle.
//
// The official Deriv Options public endpoint is a direct, unauthenticated WSS.
// The old browser runtime had a 6-second opening deadline, a fixed 700ms retry,
// and an unguarded onclose that could clear a newer replacement socket. One stale
// close could therefore make every account appear to be reconnecting forever.
// Keep exactly one current socket, ignore stale close events, use one reconnect
// timer with bounded backoff, and never stop Auto Trading for transport recovery.
// ---------------------------------------------------------------------------
let engine = read(enginePath);

if (!engine.includes("publicReconnectTimer: null,")) {
  engine = replaceOne(
    engine,
    `    publicConnectPromise: null,\n    privateConnectPromise: null,`,
    `    publicConnectPromise: null,\n    publicReconnectTimer: null,\n    publicRetryMs: 900,\n    publicReconnectAttempts: 0,\n    publicNextRetryAt: 0,\n    publicLastCloseCode: 0,\n    publicLastCloseAt: 0,\n    publicLastCloseReason: "",\n    publicLastError: "",\n    publicGeneration: 0,\n    privateConnectPromise: null,`,
    "public transport diagnostic state",
  );
}

const publicTransport = `  function clearPublicReconnectTimer() {\n    if (state.publicReconnectTimer) clearTimeout(state.publicReconnectTimer);\n    state.publicReconnectTimer = null;\n    state.publicNextRetryAt = 0;\n  }\n\n  function schedulePublicReconnect(reason = "") {\n    if (!state.running || state.publicReconnectTimer) return;\n    if (reason) state.publicLastError = String(reason).slice(0, 180);\n    const delay = Math.max(700, Math.min(15000, Number(state.publicRetryMs || 900)));\n    state.publicNextRetryAt = Date.now() + delay;\n    state.publicReconnectTimer = setTimeout(() => {\n      state.publicReconnectTimer = null;\n      state.publicNextRetryAt = 0;\n      if (!state.running) return;\n      connectPublic().catch(() => {});\n    }, delay);\n    state.publicRetryMs = Math.min(15000, Math.max(900, Math.round(delay * 1.7)));\n  }\n\n  function connectPublic() {\n    if (state.publicWs?.readyState === WebSocket.OPEN) return Promise.resolve(state.publicWs);\n    if (state.publicConnectPromise) return state.publicConnectPromise;\n\n    let ws = null;\n    try {\n      ws = new WebSocket(PUBLIC_WS_URL);\n    } catch (error) {\n      const message = "Public Deriv WebSocket constructor failed: " + String(error?.message || error || "unknown error").slice(0, 140);\n      state.publicLastError = message;\n      state.publicReconnectAttempts += 1;\n      schedulePublicReconnect(message);\n      return Promise.reject(new Error(message));\n    }\n\n    const generation = ++state.publicGeneration;\n    state.publicWs = ws;\n    let settled = false;\n    let connectPromise = null;\n\n    connectPromise = new Promise((resolve, reject) => {\n      const timer = setTimeout(() => {\n        if (settled || state.publicWs !== ws || generation !== state.publicGeneration) return;\n        const message = "Public Deriv WebSocket opening handshake exceeded 15 seconds";\n        state.publicLastError = message;\n        state.publicReconnectAttempts += 1;\n        if (state.publicConnectPromise === connectPromise) state.publicConnectPromise = null;\n        settled = true;\n        try { ws.close(); } catch (_) {}\n        schedulePublicReconnect(message);\n        reject(new Error(message));\n      }, 15000);\n\n      ws.onopen = () => {\n        if (state.publicWs !== ws || generation !== state.publicGeneration) {\n          clearTimeout(timer);\n          try { ws.close(); } catch (_) {}\n          if (!settled) { settled = true; reject(new Error("Stale public Deriv WebSocket opened after replacement")); }\n          return;\n        }\n        clearTimeout(timer);\n        clearPublicReconnectTimer();\n        if (state.publicConnectPromise === connectPromise) state.publicConnectPromise = null;\n        state.publicRetryMs = 900;\n        state.publicReconnectAttempts = 0;\n        state.publicLastCloseCode = 0;\n        state.publicLastCloseAt = 0;\n        state.publicLastCloseReason = "";\n        state.publicLastError = "";\n        state.subscribedMarkets.clear();\n        settled = true;\n        resolve(ws);\n        if (state.running) queueMicrotask(() => { try { subscribeMarkets(); } catch (_) {} });\n      };\n\n      ws.onmessage = (event) => {\n        if (state.publicWs !== ws || generation !== state.publicGeneration) return;\n        handleWsMessage("public", event);\n      };\n\n      ws.onerror = () => {\n        if (state.publicWs !== ws || generation !== state.publicGeneration) return;\n        state.publicLastError = "Public Deriv WebSocket reported a connection error";\n      };\n\n      ws.onclose = (event) => {\n        clearTimeout(timer);\n        const current = state.publicWs === ws && generation === state.publicGeneration;\n        if (!current) {\n          if (!settled) { settled = true; reject(new Error("Stale public Deriv WebSocket closed after replacement")); }\n          return;\n        }\n\n        state.publicWs = null;\n        if (state.publicConnectPromise === connectPromise) state.publicConnectPromise = null;\n        state.subscribedMarkets.clear();\n        const code = Number(event?.code || 0);\n        const reason = String(event?.reason || "").slice(0, 120);\n        state.publicLastCloseCode = code;\n        state.publicLastCloseAt = Date.now();\n        state.publicLastCloseReason = reason;\n        state.publicReconnectAttempts += 1;\n        const message = "Public Deriv WebSocket closed" + (code ? " code " + code : "") + (reason ? ": " + reason : "");\n        state.publicLastError = message;\n        rejectPending(state.publicPending, message);\n        if (!settled) { settled = true; reject(new Error(message)); }\n        schedulePublicReconnect(message);\n      };\n    });\n\n    state.publicConnectPromise = connectPromise;\n    return connectPromise;\n  }\n\n`;

engine = replaceBetween(
  engine,
  "  function connectPublic() {",
  "  function connectPrivate() {",
  publicTransport,
  "single-owner public market WebSocket transport",
);

// Exact runtime diagnosis must expose current public transport state instead of a
// generic historical message. This is diagnostic only; it does not alter strategy
// qualification or financial execution authority.
engine = replaceOne(
  engine,
  `        public_ready_state: Number(state.publicWs?.readyState ?? -1),\n        private_ready_state: Number(state.privateWs?.readyState ?? -1),`,
  `        public_ready_state: Number(state.publicWs?.readyState ?? -1),\n        public_reconnect_attempts: Number(state.publicReconnectAttempts || 0),\n        public_next_retry_at: Number(state.publicNextRetryAt || 0),\n        public_last_close_code: Number(state.publicLastCloseCode || 0),\n        public_last_close_at: Number(state.publicLastCloseAt || 0),\n        public_last_close_reason: String(state.publicLastCloseReason || ""),\n        public_last_error: String(state.publicLastError || ""),\n        private_ready_state: Number(state.privateWs?.readyState ?? -1),`,
  "public reconnect diagnostics export",
);

engine = replaceOne(
  engine,
  `    if (diagnostics.public_ready !== true) return "Auto Trading is ON; browser market-data WebSocket is reconnecting directly to Deriv.";`,
  `    if (diagnostics.public_ready !== true) {\n      const code = Number(diagnostics.public_last_close_code || 0);\n      const attempts = Number(diagnostics.public_reconnect_attempts || 0);\n      const retryAt = Number(diagnostics.public_next_retry_at || 0);\n      const waitMs = retryAt > Date.now() ? retryAt - Date.now() : 0;\n      const codeText = code ? \` code \${code}\` : "";\n      const attemptText = attempts ? \` attempt \${attempts}\` : "";\n      const waitText = waitMs > 0 ? \` • next retry in ~\${Math.ceil(waitMs / 1000)}s\` : " • retrying now";\n      const detail = String(diagnostics.public_last_error || diagnostics.public_last_close_reason || "").slice(0, 120);\n      return \`Auto Trading is ON; public Deriv market-data WebSocket is reconnecting\${codeText}\${attemptText}\${waitText}\${detail ? " • " + detail : ""}.\`;\n    }`,
  "observable public reconnect diagnosis",
);

for (const required of [
  "Public Deriv WebSocket opening handshake exceeded 15 seconds",
  "state.publicWs !== ws || generation !== state.publicGeneration",
  "publicReconnectTimer",
  "publicRetryMs",
  "public_reconnect_attempts",
  "public_last_close_code",
  "public_last_error",
  "next retry in",
]) {
  if (!engine.includes(required)) throw new Error(`public-market-websocket-v1 engine invariant missing: ${required}`);
}
if (engine.includes("setTimeout(connectPublic, 700)")) {
  throw new Error("public-market-websocket-v1 legacy fixed public reconnect loop survived");
}
if (engine.includes('reject(new Error("market stream connection delayed"))')) {
  throw new Error("public-market-websocket-v1 legacy 6-second public opening deadline survived");
}
write(enginePath, engine);

// ---------------------------------------------------------------------------
// 2. Deriv current error envelope compatibility for history hydration.
// Current Options errors use an `errors` array. Keep legacy `error` support too.
// A history validation/provider error should be surfaced to the retry loop rather
// than waiting for the timeout or being mistaken for a dead market socket.
// ---------------------------------------------------------------------------
let fence = read(fencePath);
fence = replaceOne(
  fence,
  `      if (message?.error) {\n        retryHydration(pending, message.error.message || message.error.code || "Deriv history request failed");\n        return;\n      }`,
  `      const firstError = message?.error || (Array.isArray(message?.errors) ? message.errors[0] : null);\n      if (firstError) {\n        retryHydration(pending, firstError.message || firstError.code || "Deriv history request failed");\n        return;\n      }`,
  "current Deriv errors-array history handling",
);
fence = replaceOne(
  fence,
  `      if (message?.msg_type === "history" || message?.history || message?.error) {\n        finishHydration(reqId, message);\n      }`,
  `      if (message?.msg_type === "history" || message?.history || message?.error || (Array.isArray(message?.errors) && message.errors.length)) {\n        finishHydration(reqId, message);\n      }`,
  "current Deriv errors-array history dispatch",
);
for (const required of ["Array.isArray(message?.errors)", "const firstError = message?.error"] ) {
  if (!fence.includes(required)) throw new Error(`public-market-websocket-v1 fence invariant missing: ${required}`);
}
write(fencePath, fence);

// ---------------------------------------------------------------------------
// 3. Force clients to load the corrected transport.
// ---------------------------------------------------------------------------
let index = read(indexPath);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260821-public-ws-recovery-v1",
);
index = index.replace(
  /\/direct-financial-fence-v1\.js\?v=[^"']+/g,
  "/direct-financial-fence-v1.js?v=20260821-public-history-errors-v1",
);
for (const marker of [
  "/deriv-direct-execution-v2.js?v=20260821-public-ws-recovery-v1",
  "/direct-financial-fence-v1.js?v=20260821-public-history-errors-v1",
]) {
  if (!index.includes(marker)) throw new Error(`public-market-websocket-v1 cache-bust missing: ${marker}`);
}
write(indexPath, index);

console.log("PUBLIC_MARKET_WEBSOCKET_V1_INSTALLED endpoint_unchanged=true single_current_socket=true stale_close_guard=true opening_timeout_ms=15000 bounded_backoff=true auto_trading_nonterminal=true deriv_errors_array=true");
