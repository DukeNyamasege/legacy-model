import fs from "node:fs";

const fencePath = "dist/direct-financial-fence-v1.js";
const enginePath = "dist/deriv-direct-execution-v2.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`browser-buy-readiness missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`browser-buy-readiness ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`browser-buy-readiness ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

// ---------------------------------------------------------------------------
// 1. Financial ownership and history readiness are independent.
// ---------------------------------------------------------------------------
let fence = read(fencePath);
fence = replaceOne(
  fence,
  `  function leaseAllowsBuy() {\n    return Boolean(state.armed && state.epoch && state.hydrationPending <= 0);\n  }`,
  `  function leaseAllowsBuy() {\n    return Boolean(state.armed && state.epoch);\n  }`,
  "remove global history hydration from current event-owned browser fence",
);

fence = replaceOne(
  fence,
  `    state: () => ({ ...state, buy_allowed: leaseAllowsBuy() }),`,
  `    state: () => ({\n      ...state,\n      buy_allowed: leaseAllowsBuy(),\n      ownership_ready: Boolean(state.armed && state.epoch),\n      history_pending: Math.max(0, Number(state.hydrationPending || 0)),\n    }),`,
  "export independent ownership and history readiness",
);

for (const priorVersion of [
  `version: "20260820-browser-deriv-direct-financial-fence-v3"`,
  `version: "20260821-direct-financial-fence-v3-history-preload"`,
]) {
  fence = fence.replace(
    priorVersion,
    `version: "20260821-direct-financial-fence-v5-public-recovery"`,
  );
}

// Deriv's current Options error envelope is `errors: [...]`. Keep the legacy
// single `error` form too so history hydration retries immediately instead of
// waiting for a timeout or being confused with a dead public market socket.
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

for (const required of [
  "return Boolean(state.armed && state.epoch);",
  "ownership_ready: Boolean(state.armed && state.epoch)",
  "history_pending:",
  "direct-financial-fence-v5-public-recovery",
  "Array.isArray(message?.errors)",
  "const firstError = message?.error",
]) {
  if (!fence.includes(required)) throw new Error(`browser-buy-readiness fence invariant missing: ${required}`);
}
if (fence.includes("state.armed && state.epoch && state.hydrationPending <= 0")) {
  throw new Error("browser-buy-readiness current global hydration BUY lock survived");
}
write(fencePath, fence);

// ---------------------------------------------------------------------------
// 2. Repair the shared browser public market-data WebSocket.
//
// The official Deriv Options public WSS is direct and unauthenticated. Keep that
// endpoint unchanged. The old runtime had a 6-second opening timeout, fixed 700ms
// reconnects and an unguarded old-socket `onclose` that could clear a newer socket.
// That can strand every account in "market-data WebSocket is reconnecting".
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

const publicTransport = `  function clearPublicReconnectTimer() {\n    if (state.publicReconnectTimer) clearTimeout(state.publicReconnectTimer);\n    state.publicReconnectTimer = null;\n    state.publicNextRetryAt = 0;\n  }\n\n  function schedulePublicReconnect(reason = "") {\n    if (!state.running || state.publicReconnectTimer) return;\n    if (reason) state.publicLastError = String(reason).slice(0, 180);\n    const delay = Math.max(700, Math.min(15000, Number(state.publicRetryMs || 900)));\n    state.publicNextRetryAt = Date.now() + delay;\n    state.publicReconnectTimer = setTimeout(() => {\n      state.publicReconnectTimer = null;\n      state.publicNextRetryAt = 0;\n      if (!state.running) return;\n      connectPublic().catch(() => {});\n    }, delay);\n    state.publicRetryMs = Math.min(15000, Math.max(900, Math.round(delay * 1.7)));\n  }\n\n  function connectPublic() {\n    if (state.publicWs?.readyState === WebSocket.OPEN) return Promise.resolve(state.publicWs);\n    if (state.publicConnectPromise) return state.publicConnectPromise;\n\n    let ws = null;\n    try {\n      ws = new WebSocket(PUBLIC_WS_URL);\n    } catch (error) {\n      const message = "Public Deriv WebSocket constructor failed: " + String(error?.message || error || "unknown error").slice(0, 140);\n      state.publicLastError = message;\n      state.publicReconnectAttempts += 1;\n      schedulePublicReconnect(message);\n      return Promise.reject(new Error(message));\n    }\n\n    const generation = ++state.publicGeneration;\n    state.publicWs = ws;\n    let settled = false;\n    let connectPromise = null;\n    connectPromise = new Promise((resolve, reject) => {\n      const timer = setTimeout(() => {\n        if (settled || state.publicWs !== ws || generation !== state.publicGeneration) return;\n        const message = "Public Deriv WebSocket opening handshake exceeded 15 seconds";\n        state.publicLastError = message;\n        state.publicReconnectAttempts += 1;\n        if (state.publicConnectPromise === connectPromise) state.publicConnectPromise = null;\n        settled = true;\n        try { ws.close(); } catch (_) {}\n        schedulePublicReconnect(message);\n        reject(new Error(message));\n      }, 15000);\n\n      ws.onopen = () => {\n        if (state.publicWs !== ws || generation !== state.publicGeneration) {\n          clearTimeout(timer);\n          try { ws.close(); } catch (_) {}\n          if (!settled) {\n            settled = true;\n            reject(new Error("Stale public Deriv WebSocket opened after replacement"));\n          }\n          return;\n        }\n        clearTimeout(timer);\n        clearPublicReconnectTimer();\n        if (state.publicConnectPromise === connectPromise) state.publicConnectPromise = null;\n        state.publicRetryMs = 900;\n        state.publicReconnectAttempts = 0;\n        state.publicLastCloseCode = 0;\n        state.publicLastCloseAt = 0;\n        state.publicLastCloseReason = "";\n        state.publicLastError = "";\n        state.subscribedMarkets.clear();\n        settled = true;\n        resolve(ws);\n        if (state.running) queueMicrotask(() => { try { subscribeMarkets(); } catch (_) {} });\n      };\n\n      ws.onmessage = (event) => {\n        if (state.publicWs !== ws || generation !== state.publicGeneration) return;\n        handleWsMessage("public", event);\n      };\n\n      ws.onerror = () => {\n        if (state.publicWs !== ws || generation !== state.publicGeneration) return;\n        state.publicLastError = "Public Deriv WebSocket reported a connection error";\n      };\n\n      ws.onclose = (event) => {\n        clearTimeout(timer);\n        const current = state.publicWs === ws && generation === state.publicGeneration;\n        if (!current) {\n          if (!settled) {\n            settled = true;\n            reject(new Error("Stale public Deriv WebSocket closed after replacement"));\n          }\n          return;\n        }\n\n        state.publicWs = null;\n        if (state.publicConnectPromise === connectPromise) state.publicConnectPromise = null;\n        state.subscribedMarkets.clear();\n        const code = Number(event?.code || 0);\n        const reason = String(event?.reason || "").slice(0, 120);\n        state.publicLastCloseCode = code;\n        state.publicLastCloseAt = Date.now();\n        state.publicLastCloseReason = reason;\n        state.publicReconnectAttempts += 1;\n        const message = "Public Deriv WebSocket closed" + (code ? " code " + code : "") + (reason ? ": " + reason : "");\n        state.publicLastError = message;\n        rejectPending(state.publicPending, message);\n        if (!settled) { settled = true; reject(new Error(message)); }\n        schedulePublicReconnect(message);\n      };\n    });\n\n    state.publicConnectPromise = connectPromise;\n    return connectPromise;\n  }\n\n`;
engine = replaceBetween(
  engine,
  "  function connectPublic() {",
  "  function connectPrivate() {",
  publicTransport,
  "single-owner public market WebSocket transport",
);

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

// Keep the existing ownership/history diagnosis separation from PR #45.
engine = replaceOne(
  engine,
  `    if (financial.buy_allowed === false) return "Auto Trading is ON; the browser BUY fence is temporarily not ready and is recovering.";`,
  `    if (financial.buy_allowed === false) {\n      return "Auto Trading is ON; browser financial ownership is not armed yet; Start ownership control is recovering.";\n    }\n    if (Number(financial.history_pending || financial.hydrationPending || 0) > 0) {\n      const blocker = firstConditionBlocker(diagnostics);\n      if (blocker) return blocker;\n      return "Auto Trading is ON; loading the required previous Deriv ticks before this market can qualify.";\n    }`,
  "separate financial ownership from market history diagnosis",
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
  "browser financial ownership is not armed yet",
  "loading the required previous Deriv ticks",
]) {
  if (!engine.includes(required)) throw new Error(`browser-buy-readiness engine invariant missing: ${required}`);
}
if (engine.includes("setTimeout(connectPublic, 700)")) {
  throw new Error("browser-buy-readiness legacy fixed public reconnect loop survived");
}
if (engine.includes('reject(new Error("market stream connection delayed"))')) {
  throw new Error("browser-buy-readiness legacy 6-second public opening deadline survived");
}
if (engine.includes("execution lease heartbeat is recovering")) {
  throw new Error("browser-buy-readiness obsolete heartbeat diagnosis survived");
}
write(enginePath, engine);

// ---------------------------------------------------------------------------
// 3. Force clients to load the corrected shared transport and fence.
// ---------------------------------------------------------------------------
let index = read(indexPath);
index = index.replace(
  /\/direct-financial-fence-v1\.js\?v=[^"']+/g,
  "/direct-financial-fence-v1.js?v=20260821-public-history-errors-v1",
);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260821-public-ws-recovery-v1",
);
for (const required of [
  "/direct-financial-fence-v1.js?v=20260821-public-history-errors-v1",
  "/deriv-direct-execution-v2.js?v=20260821-public-ws-recovery-v1",
]) {
  if (!index.includes(required)) throw new Error(`browser-buy-readiness cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("BUY readiness finalizer: current event-owned browser epoch no longer depends on global history hydration");
console.log("BUY readiness finalizer: shared public Deriv WSS now has stale-close protection, one retry timer and bounded recovery");
console.log("BUY readiness finalizer: Deriv errors-array history failures retry without masquerading as market-socket death");
console.log("BUY readiness finalizer: Auto Trading remains ON while public market transport recovers");
