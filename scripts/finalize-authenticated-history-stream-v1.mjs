import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`authenticated-history-stream missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceTopLevelFunction(source, name, replacement) {
  const startMarker = `  function ${name}(`;
  const start = source.indexOf(startMarker);
  if (start < 0) throw new Error(`authenticated-history-stream function missing: ${name}`);
  const next = source.indexOf("\n  function ", start + startMarker.length);
  if (next < 0) throw new Error(`authenticated-history-stream next function boundary missing after: ${name}`);
  return source.slice(0, start) + replacement.trimEnd() + "\n" + source.slice(next + 1);
}

let engine = read(enginePath);

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
  const handler = `    const marketHistory = kind === "private" && reqId ? state.marketHistoryRequests.get(reqId) : null;\n    if (marketHistory) {\n      if (payload?.error) {\n        state.marketHistoryRequests.delete(reqId);\n        state.subscribedMarkets.delete(marketHistory.symbol);\n        const message = String(payload.error?.message || payload.error?.code || "Deriv history request failed").slice(0, 180);\n        state.lastExecutionError = message;\n        window.dispatchEvent(new CustomEvent("derivadmin:direct-history-state", {\n          detail: { pending: 0, symbol: marketHistory.symbol, status: "retrying", required: marketHistory.required, received: 0, reason: message, ready: false },\n        }));\n        if (state.running && !state.ownerLost) setTimeout(() => subscribeMarkets(), 750);\n        return;\n      }\n      if (payload?.history && Array.isArray(payload.history.prices) && Array.isArray(payload.history.times)) {\n        const prices = payload.history.prices;\n        const times = payload.history.times;\n        const received = Math.min(prices.length, times.length);\n        seedHistory(marketHistory.symbol, prices, times);\n        state.marketHistoryRequests.delete(reqId);\n        window.dispatchEvent(new CustomEvent("derivadmin:direct-history-state", {\n          detail: {\n            pending: 0,\n            symbol: marketHistory.symbol,\n            status: "ready",\n            required: marketHistory.required,\n            received,\n            ready: received >= marketHistory.required,\n          },\n        }));\n        if (received >= marketHistory.required) {\n          state.lastExecutionError = "";\n          if (state.running && !state.ownerLost) updateStatus("Direct • Deriv history loaded • analyzing live ticks");\n        }\n        return;\n      }\n    }\n`;
  engine = engine.slice(0, at + reqMarker.length) + handler + engine.slice(at + reqMarker.length);
}

// On authenticated reconnect, subscription ownership must be rebuilt exactly once.
const privateCloseMarker = "          state.privateConnectPromise = null;\n          fallbackToPublicMarketTransport();\n";
if (engine.includes(privateCloseMarker) && !engine.includes("state.marketHistoryRequests.clear();\n          state.subscribedMarkets.clear();\n          fallbackToPublicMarketTransport();")) {
  engine = engine.replace(
    privateCloseMarker,
    "          state.privateConnectPromise = null;\n          state.marketHistoryRequests.clear();\n          state.subscribedMarkets.clear();\n          fallbackToPublicMarketTransport();\n",
  );
}

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
]) {
  if (!engine.includes(required)) throw new Error(`authenticated-history-stream final engine invariant missing: ${required}`);
}

// The active trading engine must not create the public WebSocket anymore.
if (engine.includes("new WebSocket(PUBLIC_WS_URL)")) {
  throw new Error("authenticated-history-stream public WebSocket constructor survived finalization");
}

write(enginePath, engine);

let index = read(indexPath);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260823-auth-history-stream-v1",
);
if (!index.includes("/deriv-direct-execution-v2.js?v=20260823-auth-history-stream-v1")) {
  throw new Error("authenticated-history-stream engine cache invariant missing");
}
write(indexPath, index);

console.log("AUTHENTICATED_HISTORY_STREAM_V1_INSTALLED public_socket_for_active_trading=false authenticated_ticks_history_subscribe=true history_req_id_owned=true echo_req_dependency=false history_and_live_same_subscription=true");
