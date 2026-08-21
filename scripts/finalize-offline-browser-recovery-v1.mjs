import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const boundaryPath = "dist/vps-api-boundary-v2.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`offline-browser-recovery missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`offline-browser-recovery ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`offline-browser-recovery ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

// ---------------------------------------------------------------------------
// 1. Public Deriv market transport must distinguish browser-offline from a Deriv
//    WebSocket failure. While offline, do not construct new sockets or run retry
//    timers. Auto Trading stays ON, and the public market stream reconnects as soon
//    as the browser emits `online`.
// ---------------------------------------------------------------------------
let engine = read(enginePath);

const publicTransport = `  function browserNetworkOnline() {\n    return navigator.onLine !== false;\n  }\n\n  function clearPublicReconnectTimer() {\n    if (state.publicReconnectTimer) clearTimeout(state.publicReconnectTimer);\n    state.publicReconnectTimer = null;\n    state.publicNextRetryAt = 0;\n  }\n\n  function markPublicOffline() {\n    clearPublicReconnectTimer();\n    state.publicNextRetryAt = 0;\n    state.publicLastError = "Browser internet connection is offline; waiting for connectivity";\n  }\n\n  function schedulePublicReconnect(reason = "") {\n    if (!state.running || state.publicReconnectTimer) return;\n    if (!browserNetworkOnline()) {\n      markPublicOffline();\n      return;\n    }\n    if (reason) state.publicLastError = String(reason).slice(0, 180);\n    const delay = Math.max(700, Math.min(15000, Number(state.publicRetryMs || 900)));\n    state.publicNextRetryAt = Date.now() + delay;\n    state.publicReconnectTimer = setTimeout(() => {\n      state.publicReconnectTimer = null;\n      state.publicNextRetryAt = 0;\n      if (!state.running || !browserNetworkOnline()) {\n        if (!browserNetworkOnline()) markPublicOffline();\n        return;\n      }\n      connectPublic().catch(() => {});\n    }, delay);\n    state.publicRetryMs = Math.min(15000, Math.max(900, Math.round(delay * 1.7)));\n  }\n\n  function connectPublic() {\n    if (state.publicWs?.readyState === WebSocket.OPEN) return Promise.resolve(state.publicWs);\n    if (state.publicConnectPromise) return state.publicConnectPromise;\n    if (!browserNetworkOnline()) {\n      markPublicOffline();\n      return Promise.reject(new Error(state.publicLastError));\n    }\n\n    let ws = null;\n    try {\n      ws = new WebSocket(PUBLIC_WS_URL);\n    } catch (error) {\n      const message = "Public Deriv WebSocket constructor failed: " + String(error?.message || error || "unknown error").slice(0, 140);\n      state.publicLastError = message;\n      state.publicReconnectAttempts += 1;\n      schedulePublicReconnect(message);\n      return Promise.reject(new Error(message));\n    }\n\n    const generation = ++state.publicGeneration;\n    state.publicWs = ws;\n    let settled = false;\n    let connectPromise = null;\n    connectPromise = new Promise((resolve, reject) => {\n      const timer = setTimeout(() => {\n        if (settled || state.publicWs !== ws || generation !== state.publicGeneration) return;\n        const message = browserNetworkOnline()\n          ? "Public Deriv WebSocket opening handshake exceeded 15 seconds"\n          : "Browser internet connection is offline; waiting for connectivity";\n        state.publicLastError = message;\n        if (browserNetworkOnline()) state.publicReconnectAttempts += 1;\n        if (state.publicConnectPromise === connectPromise) state.publicConnectPromise = null;\n        settled = true;\n        try { ws.close(); } catch (_) {}\n        if (browserNetworkOnline()) schedulePublicReconnect(message);\n        else markPublicOffline();\n        reject(new Error(message));\n      }, 15000);\n\n      ws.onopen = () => {\n        if (state.publicWs !== ws || generation !== state.publicGeneration) {\n          clearTimeout(timer);\n          try { ws.close(); } catch (_) {}\n          if (!settled) {\n            settled = true;\n            reject(new Error("Stale public Deriv WebSocket opened after replacement"));\n          }\n          return;\n        }\n        clearTimeout(timer);\n        clearPublicReconnectTimer();\n        if (state.publicConnectPromise === connectPromise) state.publicConnectPromise = null;\n        state.publicRetryMs = 900;\n        state.publicReconnectAttempts = 0;\n        state.publicLastCloseCode = 0;\n        state.publicLastCloseAt = 0;\n        state.publicLastCloseReason = "";\n        state.publicLastError = "";\n        state.subscribedMarkets.clear();\n        settled = true;\n        resolve(ws);\n        if (state.running) queueMicrotask(() => { try { subscribeMarkets(); } catch (_) {} });\n      };\n\n      ws.onmessage = (event) => {\n        if (state.publicWs !== ws || generation !== state.publicGeneration) return;\n        handleWsMessage("public", event);\n      };\n\n      ws.onerror = () => {\n        if (state.publicWs !== ws || generation !== state.publicGeneration) return;\n        state.publicLastError = browserNetworkOnline()\n          ? "Public Deriv WebSocket reported a connection error"\n          : "Browser internet connection is offline; waiting for connectivity";\n      };\n\n      ws.onclose = (event) => {\n        clearTimeout(timer);\n        const current = state.publicWs === ws && generation === state.publicGeneration;\n        if (!current) {\n          if (!settled) {\n            settled = true;\n            reject(new Error("Stale public Deriv WebSocket closed after replacement"));\n          }\n          return;\n        }\n\n        state.publicWs = null;\n        if (state.publicConnectPromise === connectPromise) state.publicConnectPromise = null;\n        state.subscribedMarkets.clear();\n        const code = Number(event?.code || 0);\n        const reason = String(event?.reason || "").slice(0, 120);\n        state.publicLastCloseCode = code;\n        state.publicLastCloseAt = Date.now();\n        state.publicLastCloseReason = reason;\n\n        if (!browserNetworkOnline()) {\n          const message = "Browser internet connection is offline; waiting for connectivity";\n          state.publicLastError = message;\n          rejectPending(state.publicPending, message);\n          if (!settled) { settled = true; reject(new Error(message)); }\n          markPublicOffline();\n          return;\n        }\n\n        state.publicReconnectAttempts += 1;\n        const message = "Public Deriv WebSocket closed" + (code ? " code " + code : "") + (reason ? ": " + reason : "");\n        state.publicLastError = message;\n        rejectPending(state.publicPending, message);\n        if (!settled) { settled = true; reject(new Error(message)); }\n        schedulePublicReconnect(message);\n      };\n    });\n\n    state.publicConnectPromise = connectPromise;\n    return connectPromise;\n  }\n\n  window.addEventListener("offline", () => {\n    markPublicOffline();\n    const ws = state.publicWs;\n    if (ws && ws.readyState < WebSocket.CLOSING) {\n      try { ws.close(1000, "browser offline"); } catch (_) {}\n    }\n  });\n\n  window.addEventListener("online", () => {\n    if (!state.running) return;\n    clearPublicReconnectTimer();\n    state.publicRetryMs = 900;\n    state.publicReconnectAttempts = 0;\n    state.publicLastError = "Browser internet connection restored; reconnecting public Deriv market data";\n    connectPublic().catch(() => {});\n  });\n\n`;

const publicTransportEndMarker = engine.includes("  function clearDirectBrowserCredential() {")
  ? "  function clearDirectBrowserCredential() {"
  : "  function connectPrivate() {";
engine = replaceBetween(
  engine,
  engine.includes("  function browserNetworkOnline() {")
    ? "  function browserNetworkOnline() {"
    : "  function clearPublicReconnectTimer() {",
  publicTransportEndMarker,
  publicTransport,
  "offline-aware public Deriv transport",
);

engine = replaceOne(
  engine,
  `        public_ready_state: Number(state.publicWs?.readyState ?? -1),\n        public_reconnect_attempts: Number(state.publicReconnectAttempts || 0),`,
  `        public_ready_state: Number(state.publicWs?.readyState ?? -1),\n        browser_online: navigator.onLine !== false,\n        public_reconnect_attempts: Number(state.publicReconnectAttempts || 0),`,
  "browser online diagnostic",
);

engine = replaceOne(
  engine,
  `    if (diagnostics.public_ready !== true) {\n`,
  `    if (diagnostics.browser_online === false) {\n      return "Auto Trading is ON; this browser is offline. Waiting for internet connection; execution will resume automatically when connectivity returns.";\n    }\n    if (diagnostics.public_ready !== true) {\n`,
  "offline no-purchase diagnosis",
);

for (const required of [
  'const PUBLIC_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"',
  "function browserNetworkOnline()",
  "navigator.onLine !== false",
  "Browser internet connection is offline; waiting for connectivity",
  'window.addEventListener("online"',
  'window.addEventListener("offline"',
  "browser_online: navigator.onLine !== false",
  "execution will resume automatically when connectivity returns",
  "new WebSocket(PUBLIC_WS_URL)",
]) {
  if (!engine.includes(required)) throw new Error(`offline-browser-recovery engine invariant missing: ${required}`);
}
write(enginePath, engine);

// ---------------------------------------------------------------------------
// 2. Same-origin dashboard polling must not hammer the network while Chrome has
//    already declared the machine offline. Read-only runtime screens can use their
//    last VPS live cache; writes fail locally with a retryable 503 and never pretend
//    to have reached the VPS.
// ---------------------------------------------------------------------------
let boundary = read(boundaryPath);
if (!boundary.includes("function offlineCachedResponse(path)")) {
  boundary = replaceOne(
    boundary,
    `  window.fetch = async (input, options = {}) => {\n`,
    `  function offlineCachedResponse(path) {\n    const route = unproxiedRouteOf(path);\n    const cache = window.FOA_VPS_LIVE_CACHE || {};\n    const headers = { "X-DerivAdmin-Source": "offline-live-cache", "X-DerivAdmin-Offline": "1" };\n    if (route === "/me/live-snapshot") {\n      return responseJSON({\n        me: cache.me || lastMe || null,\n        lifecycle: cache.lifecycle || null,\n        trades: cache.trades || null,\n        offline: true,\n        stale: true,\n      }, 200, headers);\n    }\n    if (route === "/me" && (cache.me || lastMe)) return responseJSON(cache.me || lastMe, 200, headers);\n    if ((route === "/me/trading-lifecycle" || route === "/me/execution-runtime") && cache.lifecycle) return responseJSON(cache.lifecycle, 200, headers);\n    if (route === "/me/trades/today" && cache.trades) return responseJSON(cache.trades, 200, headers);\n    return null;\n  }\n\n  window.fetch = async (input, options = {}) => {\n`,
    "offline cache helper",
  );
}

boundary = replaceOne(
  boundary,
  `    const method = String(options.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();\n    const local = await localPreviewResponse(path, options);\n`,
  `    const method = String(options.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();\n    const requestUrl = asURL(input);\n    const sameOrigin = Boolean(requestUrl && requestUrl.origin === window.location.origin);\n    if (sameOrigin && navigator.onLine === false) {\n      if (method === "GET" || method === "HEAD") {\n        const cached = offlineCachedResponse(path);\n        if (cached) return cached;\n      }\n      return responseJSON({\n        detail: "Browser is offline. Request was not sent and can be retried when connectivity returns.",\n        offline: true,\n        retryable: true,\n      }, 503, { "X-DerivAdmin-Offline": "1" });\n    }\n    const local = await localPreviewResponse(path, options);\n`,
  "offline same-origin fetch fence",
);

for (const required of [
  "function offlineCachedResponse(path)",
  "sameOrigin && navigator.onLine === false",
  'route === "/me/live-snapshot"',
  '"X-DerivAdmin-Offline": "1"',
  "Request was not sent and can be retried when connectivity returns",
]) {
  if (!boundary.includes(required)) throw new Error(`offline-browser-recovery API boundary invariant missing: ${required}`);
}
write(boundaryPath, boundary);

// ---------------------------------------------------------------------------
// 3. Cache-bust the exact two browser authorities changed here.
// ---------------------------------------------------------------------------
let index = read(indexPath);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260821-offline-aware-recovery-v1",
);
index = index.replace(
  /\/vps-api-boundary-v2\.js\?v=[^"']+/g,
  "/vps-api-boundary-v2.js?v=20260821-offline-aware-v1",
);
for (const required of [
  "/deriv-direct-execution-v2.js?v=20260821-offline-aware-recovery-v1",
  "/vps-api-boundary-v2.js?v=20260821-offline-aware-v1",
]) {
  if (!index.includes(required)) throw new Error(`offline-browser-recovery cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("OFFLINE_BROWSER_RECOVERY_V1_INSTALLED public_deriv_endpoint_unchanged=true offline_socket_retries_paused=true online_resume_immediate=true same_origin_polling_cached=true writes_fail_closed=true auto_trading_nonterminal=true");
