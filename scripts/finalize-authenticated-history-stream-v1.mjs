import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const fencePath = "dist/direct-financial-fence-v1.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`authenticated-history-stream missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) {
    throw new Error(`authenticated-history-stream ${label}: expected 1 source match or installed shape, got ${count}`);
  }
  return source.replace(before, after);
}

function replaceTopLevelFunction(source, name, replacement) {
  const startMarker = `  function ${name}(`;
  const start = source.indexOf(startMarker);
  if (start < 0) throw new Error(`authenticated-history-stream function missing: ${name}`);

  // Top-level helpers can be either `function` or `async function`. The previous
  // boundary searched only for plain `function`, so replacing connectPublic()
  // skipped over `async function fetchWithTimeout(...)` and deleted it from the
  // shipped engine. Recognize both declaration forms at the same indentation.
  const boundary = /\n  (?:async )?function /g;
  boundary.lastIndex = start + startMarker.length;
  const nextMatch = boundary.exec(source);
  const next = nextMatch?.index ?? -1;
  if (next < 0) throw new Error(`authenticated-history-stream next function boundary missing after: ${name}`);
  return source.slice(0, start) + replacement.trimEnd() + "\n" + source.slice(next + 1);
}

let engine = read(enginePath);
let fence = read(fencePath);

// The current Deriv Options API allows ticks_history to subscribe directly.
// One authenticated demo/real WebSocket therefore owns:
//   1) required historical ticks,
//   2) the continuing live tick stream,
//   3) proposal/BUY/contract updates.
// This removes the public WebSocket from the active trading lifecycle completely.
// It also avoids relying on echo_req, which the new Deriv API no longer guarantees.
if (!engine.includes("marketHistoryRequests: new Map(),")) {
  const marker = "    subscribedMarkets: new Set(),\n";
  if (!engine.includes(marker)) throw new Error("authenticated-history-stream subscribedMarkets state marker missing");
  engine = engine.replace(marker, marker + "    marketHistoryRequests: new Map(),\n");
}

const connectPublicReplacement = `  function connectPublic() {
    // Active browser trading no longer creates a public market-data socket.
    // The OTP-authenticated Options socket is the single execution + market-data
    // transport. Keeping this function as a resolved no-op preserves older callers
    // without opening a second WebSocket or producing CONNECTING->CLOSED warnings.
    return Promise.resolve(null);
  }
`;
engine = replaceTopLevelFunction(engine, "connectPublic", connectPublicReplacement);

// The final authenticated-history mutation runs after the fetch-timeout finalizer.
// It must preserve the bounded REST helper used by bootstrap, /arm and runtime-sync.
const timeoutHelperMarker = "  async function fetchWithTimeout(";
if ((engine.split(timeoutHelperMarker).length - 1) !== 1) {
  throw new Error("authenticated-history-stream must preserve exactly one fetchWithTimeout helper after connectPublic replacement");
}

const subscribeReplacement = `  function requiredMarketHistoryCount() {
    const windows = [];
    const primary = Array.isArray(state.strategy?.conditions) ? state.strategy.conditions : [];
    const recovery = Array.isArray(state.strategy?.result_routing?.after_loss?.conditions)
      ? state.strategy.result_routing.after_loss.conditions
      : [];
    for (const condition of [...primary, ...recovery]) {
      const value = Math.trunc(Number(condition?.window || 1));
      if (Number.isFinite(value)) windows.push(value);
    }
    return Math.max(1, Math.min(1000, ...(windows.length ? windows : [1])));
  }

  function sendAuthenticatedHistoryStream(symbol) {
    const ws = state.privateWs;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    const required = requiredMarketHistoryCount();
    const reqId = ++state.privateReq;
    state.marketHistoryRequests.set(reqId, { symbol, required, startedAt: Date.now() });
    try {
      ws.send(JSON.stringify({
        ticks_history: symbol,
        count: required,
        end: "latest",
        style: "ticks",
        subscribe: 1,
        req_id: reqId,
      }));
      window.dispatchEvent(new CustomEvent("derivadmin:direct-history-state", {
        detail: { pending: 1, symbol, status: "loading", required, received: 0, ready: false },
      }));
      return true;
    } catch (error) {
      state.marketHistoryRequests.delete(reqId);
      state.lastExecutionError = String(error?.message || error || "Deriv history subscription failed").slice(0, 180);
      return false;
    }
  }

  function subscribeMarkets() {
    if (!executionTransportReady() || !state.strategy) return;
    state.marketDataKind = "private";
    for (const symbol of state.strategy.markets) {
      if (state.subscribedMarkets.has(symbol)) continue;
      const alreadyPending = Array.from(state.marketHistoryRequests.values()).some((row) => row?.symbol === symbol);
      if (alreadyPending) continue;
      if (sendAuthenticatedHistoryStream(symbol)) state.subscribedMarkets.add(symbol);
    }
  }
`;
engine = replaceTopLevelFunction(engine, "subscribeMarkets", subscribeReplacement);

const reqMarker = "    const reqId = Number(payload.req_id || 0);\n";
if (!engine.includes("const marketHistory = kind === \"private\"")) {
  const at = engine.indexOf(reqMarker);
  if (at < 0) throw new Error("authenticated-history-stream req_id message boundary missing");
  const handler = `    const marketHistory = kind === "private" && reqId ? state.marketHistoryRequests.get(reqId) : null;\n    if (marketHistory) {\n      const firstError = payload?.error || (Array.isArray(payload?.errors) ? payload.errors[0] : null);\n      if (firstError) {\n        state.marketHistoryRequests.delete(reqId);\n        state.subscribedMarkets.delete(marketHistory.symbol);\n        const message = String(firstError?.message || firstError?.code || "Deriv history request failed").slice(0, 180);\n        state.lastExecutionError = message;\n        window.dispatchEvent(new CustomEvent("derivadmin:direct-history-state", {\n          detail: { pending: 0, symbol: marketHistory.symbol, status: "retrying", required: marketHistory.required, received: 0, reason: message, ready: false },\n        }));\n        if (state.running && !state.ownerLost) setTimeout(() => subscribeMarkets(), 750);\n        return;\n      }\n      if (payload?.history && Array.isArray(payload.history.prices) && Array.isArray(payload.history.times)) {\n        const prices = payload.history.prices;\n        const times = payload.history.times;\n        const received = Math.min(prices.length, times.length);\n        seedHistory(marketHistory.symbol, prices, times);\n        state.marketHistoryRequests.delete(reqId);\n        window.dispatchEvent(new CustomEvent("derivadmin:direct-history-state", {\n          detail: {\n            pending: 0,\n            symbol: marketHistory.symbol,\n            status: "ready",\n            required: marketHistory.required,\n            received,\n            ready: received >= marketHistory.required,\n          },\n        }));\n        if (received >= marketHistory.required) {\n          state.lastExecutionError = "";\n          if (state.running && !state.ownerLost) updateStatus("Direct • Deriv history loaded • analyzing live ticks");\n        }\n        return;\n      }\n    }\n`;
  engine = engine.slice(0, at + reqMarker.length) + handler + engine.slice(at + reqMarker.length);
}

// Start must not initiate a legacy public-market path before ownership is armed.
// armInBackground() confirms the browser epoch first, opens the authenticated Deriv
// Options socket, and then subscribeMarkets() starts history + live ticks there.
engine = replaceOne(
  engine,
  `    connectPublic().then(subscribeMarkets).catch(() => {});\n    armInBackground(state.epoch, strategy);`,
  `    // Ownership first; authenticated Deriv history/live/trading follows after arm.\n    armInBackground(state.epoch, strategy);`,
  "arm-first manual Start",
);

// On authenticated reconnect, subscription ownership must be rebuilt exactly once.
const privateCloseMarker = "          state.privateConnectPromise = null;\n          fallbackToPublicMarketTransport();\n";
if (engine.includes(privateCloseMarker) && !engine.includes("state.marketHistoryRequests.clear();\n          state.subscribedMarkets.clear();\n          fallbackToPublicMarketTransport();")) {
  engine = engine.replace(
    privateCloseMarker,
    "          state.privateConnectPromise = null;\n          state.marketHistoryRequests.clear();\n          state.subscribedMarkets.clear();\n          fallbackToPublicMarketTransport();\n",
  );
}

// A timed-out POST /arm can commit on the control plane after the browser request
// aborts. /runtime-sync then proves the same browser epoch. The execution engine
// already accepts that proof, but the independent BUY fence may never have observed
// the successful POST response. Reconcile both authorities from the same payload.
if (!fence.includes("function acceptReconciledArm(epoch, payload)")) {
  const marker = "  window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1 = Object.freeze({";
  const at = fence.indexOf(marker);
  if (at < 0) throw new Error("authenticated-history-stream financial fence export boundary missing");
  const helper = `  function acceptReconciledArm(epoch, payload) {\n    const expectedEpoch = String(epoch || \"\");\n    if (!expectedEpoch) return false;\n    const sameEpoch = String(payload?.epoch || \"\") === expectedEpoch;\n    const owner = String(payload?.owner || \"\").toLowerCase();\n    const status = String(payload?.execution_status || \"\").toLowerCase();\n    const browserOwned = owner === \"browser\"\n      && (status === \"direct_browser\" || status === \"browser_direct\" || status === \"browser_direct_only\");\n    const financiallyAllowed = payload?.purchase_allowed !== false\n      && payload?.hard_stop !== true\n      && payload?.enabled !== false;\n    if (!sameEpoch || !browserOwned || !financiallyAllowed) return false;\n    const now = Date.now();\n    state.armed = true;\n    state.epoch = expectedEpoch;\n    state.armedAt = now;\n    state.lastAckAt = now;\n    state.leaseMs = Number.MAX_SAFE_INTEGER;\n    return true;\n  }\n\n`;
  fence = fence.slice(0, at) + helper + fence.slice(at);
}

fence = replaceOne(
  fence,
  `    state: () => ({`,
  `    accept_reconciled_arm: acceptReconciledArm,\n    state: () => ({`,
  "export reconciled Start ownership handoff",
);

engine = replaceOne(
  engine,
  `    if (!sameEpoch || !browserOwned || !financiallyAllowed) return false;\n    state.armed = true;`,
  `    if (!sameEpoch || !browserOwned || !financiallyAllowed) return false;\n    const financialFence = window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1;\n    if (typeof financialFence?.accept_reconciled_arm !== "function"\n        || financialFence.accept_reconciled_arm(epoch, payload) !== true) {\n      state.lastExecutionError = "Financial ownership fence rejected the reconciled Start epoch";\n      return false;\n    }\n    state.armed = true;`,
  "synchronize reconciled Start with financial fence",
);

for (const required of [
  "marketHistoryRequests: new Map()",
  "function sendAuthenticatedHistoryStream(symbol)",
  'ticks_history: symbol',
  'subscribe: 1',
  'end: "latest"',
  'style: "ticks"',
  "state.marketHistoryRequests.get(reqId)",
  "seedHistory(marketHistory.symbol, prices, times)",
  'state.marketDataKind = "private"',
  "return Promise.resolve(null)",
  "async function fetchWithTimeout(url, options, timeoutMs)",
  "armInBackground(state.epoch, strategy);",
  "function acceptReconciledArm(epoch, payload)",
  "accept_reconciled_arm: acceptReconciledArm",
  "financialFence.accept_reconciled_arm(epoch, payload)",
  "Financial ownership fence rejected the reconciled Start epoch",
]) {
  if (!(engine.includes(required) || fence.includes(required))) {
    throw new Error(`authenticated-history-stream final invariant missing: ${required}`);
  }
}

if (engine.includes("new WebSocket(PUBLIC_WS_URL)")) {
  throw new Error("authenticated-history-stream public WebSocket constructor survived finalization");
}
if (engine.includes(`connectPublic().then(subscribeMarkets).catch(() => {});\n    armInBackground(state.epoch, strategy);`)) {
  throw new Error("authenticated-history-stream pre-arm public Start path survived finalization");
}
if ((engine.split(timeoutHelperMarker).length - 1) !== 1) {
  throw new Error("authenticated-history-stream final artifact lost or duplicated fetchWithTimeout");
}

write(enginePath, engine);
write(fencePath, fence);

let index = read(indexPath);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260823-auth-history-start-v3",
);
index = index.replace(
  /\/direct-financial-fence-v1\.js\?v=[^"']+/g,
  "/direct-financial-fence-v1.js?v=20260823-reconciled-ownership-v3",
);
for (const required of [
  "/deriv-direct-execution-v2.js?v=20260823-auth-history-start-v3",
  "/direct-financial-fence-v1.js?v=20260823-reconciled-ownership-v3",
]) {
  if (!index.includes(required)) throw new Error(`authenticated-history-stream cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("AUTHENTICATED_HISTORY_STREAM_V3_INSTALLED public_socket_for_active_trading=false authenticated_ticks_history_subscribe=true history_req_id_owned=true echo_req_dependency=false history_and_live_same_subscription=true start_arm_first=true reconciled_financial_fence_sync=true fetch_timeout_helper_preserved=true");
