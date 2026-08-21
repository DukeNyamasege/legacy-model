import fs from "node:fs";

const fencePath = "dist/direct-financial-fence-v1.js";
const enginePath = "dist/deriv-direct-execution-v2.js";
const ledgerPath = "dist/direct-transaction-ledger-v6.js";
const shellPath = "dist/final-ui-shell-v2.js";
const premiumPath = "dist/final-premium-6f3.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`history-preload-runpanel missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`history-preload-runpanel ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`history-preload-runpanel ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

// ---------------------------------------------------------------------------
// 1. Provider history is mandatory before the live subscription.
//
// Deriv supports ticks_history with count/end/style and a separate live ticks
// subscription. The previous fence abandoned history after 3.5 seconds and then
// started live ticks anyway. A delayed response therefore made 1,000-tick rules
// rebuild one future tick at a time. Keep BUY fenced, retry history, hydrate the
// requested strategy window, and only then start the live rolling stream.
// ---------------------------------------------------------------------------
let fence = read(fencePath);
fence = replaceOne(
  fence,
  `  let hydrationReq = 900000000;\n  let balanceReq = 850000000;`,
  `  let hydrationReq = 900000000;\n  let balanceReq = 850000000;\n  const HISTORY_MAX_COUNT = 1000;\n  const HISTORY_RESPONSE_TIMEOUT_MS = 10000;\n  const HISTORY_RETRY_DELAY_MS = 900;`,
  "history retry constants",
);

const hydrationHelpers = `    function requiredHistoryCount() {\n      try {\n        const snapshot = window.DERIVADMIN_DIRECT_EXECUTION_V1?.state?.() || {};\n        const strategy = snapshot.strategy || {};\n        const conditionSets = [\n          snapshot.active_route?.conditions,\n          strategy.conditions,\n          strategy.result_routing?.after_loss?.conditions,\n        ];\n        let required = 1;\n        for (const conditions of conditionSets) {\n          for (const condition of Array.isArray(conditions) ? conditions : []) {\n            const windowSize = Math.trunc(Number(condition?.window || 1));\n            if (Number.isFinite(windowSize)) required = Math.max(required, windowSize);\n          }\n        }\n        return Math.max(1, Math.min(HISTORY_MAX_COUNT, required));\n      } catch (_) {\n        return HISTORY_MAX_COUNT;\n      }\n    }\n\n    function emitHistoryState(pending, status, received = 0, reason = \"\") {\n      window.dispatchEvent(new CustomEvent(\"derivadmin:direct-history-state\", {\n        detail: {\n          pending: state.hydrationPending,\n          symbol: pending.symbol,\n          status,\n          required: pending.required,\n          received,\n          attempt: pending.attempts,\n          reason: String(reason || \"\").slice(0, 140),\n          ready: status === \"ready\",\n        },\n      }));\n    }\n\n    function releaseHydration(pending, startLive) {\n      if (!pending || pending.released) return;\n      pending.released = true;\n      clearTimeout(pending.timer);\n      if (pending.reqId) historyRequests.delete(pending.reqId);\n      state.hydrationPending = Math.max(0, state.hydrationPending - 1);\n      emitHistoryState(pending, startLive ? \"ready\" : \"cancelled\", pending.received || 0);\n      if (startLive && socket.readyState === NativeWebSocket.OPEN) {\n        try { nativeSend(pending.livePayload); } catch (_) {}\n      }\n    }\n\n    function retryHydration(pending, reason) {\n      if (!pending || pending.released) return;\n      clearTimeout(pending.timer);\n      if (pending.reqId) historyRequests.delete(pending.reqId);\n      pending.reqId = 0;\n      emitHistoryState(pending, \"retrying\", pending.received || 0, reason);\n      if (socket.readyState !== NativeWebSocket.OPEN) {\n        releaseHydration(pending, false);\n        return;\n      }\n      pending.timer = setTimeout(() => requestHydration(pending), HISTORY_RETRY_DELAY_MS);\n    }\n\n    function requestHydration(pending) {\n      if (!pending || pending.released) return;\n      if (socket.readyState !== NativeWebSocket.OPEN) {\n        releaseHydration(pending, false);\n        return;\n      }\n      pending.attempts += 1;\n      const reqId = ++hydrationReq;\n      pending.reqId = reqId;\n      historyRequests.set(reqId, pending);\n      emitHistoryState(pending, pending.attempts === 1 ? \"loading\" : \"retrying\", pending.received || 0);\n      try {\n        nativeSend(JSON.stringify({\n          ticks_history: pending.symbol,\n          count: pending.required,\n          end: \"latest\",\n          style: \"ticks\",\n          subscribe: 0,\n          req_id: reqId,\n        }));\n      } catch (error) {\n        retryHydration(pending, error?.message || \"history request send failed\");\n        return;\n      }\n      pending.timer = setTimeout(() => {\n        if (pending.released || pending.reqId !== reqId) return;\n        retryHydration(pending, \`history response timeout after \${HISTORY_RESPONSE_TIMEOUT_MS / 1000}s\`);\n      }, HISTORY_RESPONSE_TIMEOUT_MS);\n    }\n\n    function finishHydration(reqId, message = null) {\n      const pending = historyRequests.get(reqId);\n      if (!pending || pending.released || pending.reqId !== reqId) return;\n      clearTimeout(pending.timer);\n      historyRequests.delete(reqId);\n      pending.reqId = 0;\n\n      if (message?.error) {\n        retryHydration(pending, message.error.message || message.error.code || \"Deriv history request failed\");\n        return;\n      }\n      const prices = Array.isArray(message?.history?.prices) ? message.history.prices : [];\n      const times = Array.isArray(message?.history?.times) ? message.history.times : [];\n      const count = Math.min(prices.length, times.length);\n      pending.received = count;\n      if (count < pending.required) {\n        retryHydration(pending, \`history incomplete \${count}/\${pending.required}\`);\n        return;\n      }\n\n      const pipSize = Number(message?.pip_size);\n      for (let index = count - pending.required; index < count; index += 1) {\n        const tick = {\n          symbol: pending.symbol,\n          quote: prices[index],\n          epoch: Number(times[index]),\n          __history_hydration: true,\n        };\n        if (Number.isInteger(pipSize) && pipSize >= 0) tick.pip_size = pipSize;\n        try {\n          socket.dispatchEvent(new MessageEvent(\"message\", {\n            data: JSON.stringify({ msg_type: \"tick\", tick }),\n          }));\n        } catch (_) {}\n      }\n      releaseHydration(pending, true);\n    }\n\n`;
fence = replaceBetween(
  fence,
  `    function finishHydration(reqId, message = null) {`,
  `    socket.addEventListener(\"message\", (event) => {`,
  hydrationHelpers,
  "mandatory provider history hydration",
);

fence = replaceOne(
  fence,
  `      socket.addEventListener(\"close\", () => {\n        for (const reqId of Array.from(historyRequests.keys())) finishHydration(reqId, null);\n      });`,
  `      socket.addEventListener(\"close\", () => {\n        const pendingRows = new Set(historyRequests.values());\n        historyRequests.clear();\n        for (const pending of pendingRows) releaseHydration(pending, false);\n      });`,
  "socket close cancels history without live fallback",
);

const oldSubscribeHydration = `      if (publicSocket && payload?.ticks && Number(payload?.subscribe || 0) === 1) {\n        const symbol = String(payload.ticks || \"\").toUpperCase();\n        const reqId = ++hydrationReq;\n        state.hydrationPending += 1;\n        window.dispatchEvent(new CustomEvent(\"derivadmin:direct-history-state\", {\n          detail: { pending: state.hydrationPending, symbol },\n        }));\n        const timer = setTimeout(() => finishHydration(reqId, null), 3500);\n        historyRequests.set(reqId, {\n          symbol,\n          livePayload: data,\n          timer,\n        });\n        try {\n          nativeSend(JSON.stringify({\n            ticks_history: symbol,\n            count: 1001,\n            end: \"latest\",\n            style: \"ticks\",\n            subscribe: 0,\n            req_id: reqId,\n          }));\n          return;\n        } catch (_) {\n          finishHydration(reqId, null);\n          return;\n        }\n      }`;
const newSubscribeHydration = `      if (publicSocket && payload?.ticks && Number(payload?.subscribe || 0) === 1) {\n        const symbol = String(payload.ticks || \"\").toUpperCase();\n        const pending = {\n          symbol,\n          livePayload: data,\n          required: requiredHistoryCount(),\n          received: 0,\n          attempts: 0,\n          reqId: 0,\n          timer: null,\n          released: false,\n        };\n        state.hydrationPending += 1;\n        requestHydration(pending);\n        return;\n      }`;
fence = replaceOne(fence, oldSubscribeHydration, newSubscribeHydration, "history-before-live subscription");
fence = fence.replace(
  `version: \"20260818-direct-financial-fence-v2\"`,
  `version: \"20260821-direct-financial-fence-v3-history-preload\"`,
);
for (const required of [
  "HISTORY_RESPONSE_TIMEOUT_MS = 10000",
  "count: pending.required",
  'end: "latest"',
  'style: "ticks"',
  "retryHydration(pending",
  "history incomplete",
  "releaseHydration(pending, true)",
]) {
  if (!fence.includes(required)) throw new Error(`history-preload-runpanel fence invariant missing: ${required}`);
}
if (fence.includes("setTimeout(() => finishHydration(reqId, null), 3500)")) {
  throw new Error("history-preload-runpanel old 3.5s live fallback survived");
}
write(fencePath, fence);

// ---------------------------------------------------------------------------
// 2. The generated execution engine keeps a true rolling provider window.
// Ignore duplicate/out-of-order epochs at the history/live seam, then let the
// existing MAX_HISTORY shift logic evict the oldest tick for each new live tick.
// ---------------------------------------------------------------------------
let engine = read(enginePath);
engine = replaceOne(
  engine,
  `    const market = historyState(symbol);\n    const pipSize = window.DERIVADMIN_DIRECT_PIP_PRECISION_V1?.pip_size?.(symbol) ?? null;`,
  `    const market = historyState(symbol);\n    if (epoch > 0 && market.lastEpoch > 0 && epoch <= market.lastEpoch) return null;\n    const pipSize = window.DERIVADMIN_DIRECT_PIP_PRECISION_V1?.pip_size?.(symbol) ?? null;`,
  "rolling history duplicate seam guard",
);

engine = replaceOne(
  engine,
  `      return \`Waiting for exact Builder history on \${waitingMarket.symbol}: \${row.label} — \${row.observed}.\`;`,
  `      return \`Bot running • loading Deriv history • \${waitingMarket.symbol} • \${row.label} • \${row.observed}.\`;`,
  "history diagnostic wording",
);
engine = replaceOne(
  engine,
  `      if (row) return \`Exact Builder condition not met on \${blocked.symbol}: \${row.label} — observed \${row.observed}.\`;`,
  `      if (row) return \`Bot running • condition not met • \${blocked.symbol} • \${row.label} • observed \${row.observed}.\`;`,
  "condition blocker wording",
);
engine = replaceOne(
  engine,
  `    return state.lastBlockingReason || \"Exact Builder conditions are not currently met; waiting for a qualifying live tick.\";`,
  `    return state.lastBlockingReason || \"Bot running • condition not met • waiting for a qualifying live tick.\";`,
  "condition fallback wording",
);
for (const required of [
  "epoch <= market.lastEpoch) return null",
  "Bot running • loading Deriv history",
  "Bot running • condition not met",
]) {
  if (!engine.includes(required)) throw new Error(`history-preload-runpanel engine invariant missing: ${required}`);
}
write(enginePath, engine);

// ---------------------------------------------------------------------------
// 3. Run totals are a panel header, not a Summary-only footer.
// Move the same stats block above the scrollable body so Summary, Transactions
// and Journal all retain Runs/Wins/Losses/P&L in view.
// ---------------------------------------------------------------------------
let shell = read(shellPath);
shell = replaceOne(
  shell,
  `        <div class=\"run-panel-tabs\">\${runPanelTabs(activeTab)}</div>\n        <div class=\"run-panel-body\">\${runPanelContent(activeTab, stats, currency, running)}</div>\n        <div class=\"run-panel-stats\">\${runPanelStatsMarkup(stats, currency)}</div>`,
  `        <div class=\"run-panel-tabs\">\${runPanelTabs(activeTab)}</div>\n        <div class=\"run-panel-stats\" data-run-uniform-metrics=\"true\">\${runPanelStatsMarkup(stats, currency)}</div>\n        <div class=\"run-panel-body\">\${runPanelContent(activeTab, stats, currency, running)}</div>`,
  "uniform run metrics placement",
);
if (!shell.includes('data-run-uniform-metrics="true"')) throw new Error("uniform run metrics marker missing");
write(shellPath, shell);

// The direct ledger has the freshest browser-direct contracts. Let it refresh the
// same stats strip even when Journal or Summary is selected, while retaining body
// ownership only for Transactions.
let ledger = read(ledgerPath);
ledger = replaceOne(
  ledger,
  `  function render(force = false) {\n    if (!activeTransactions()) { connectObserver(); return; }`,
  `  function render(force = false) {\n    if (!activeTransactions()) {\n      const summary = document.querySelector(\".global-run-panel .run-panel-stats\");\n      if (summary) {\n        applying = true;\n        disconnectObserver();\n        try { summary.innerHTML = statsMarkup(stats(contracts())); }\n        finally { applying = false; connectObserver(); }\n      } else connectObserver();\n      return;\n    }`,
  "ledger metrics across every tab",
);
ledger = replaceOne(
  ledger,
  `  document.addEventListener(\"click\", (event) => {\n    if (event.target?.closest?.('[data-run-tab=\"transactions\"]')) queueMicrotask(renderNow);\n  });`,
  `  document.addEventListener(\"click\", (event) => {\n    if (event.target?.closest?.(\".global-run-panel [data-run-tab]\")) queueMicrotask(renderNow);\n  });`,
  "all run tabs refresh ledger metrics",
);
for (const required of [
  "summary.innerHTML = statsMarkup(stats(contracts()))",
  '.global-run-panel [data-run-tab]',
]) {
  if (!ledger.includes(required)) throw new Error(`history-preload-runpanel ledger invariant missing: ${required}`);
}
write(ledgerPath, ledger);

// ---------------------------------------------------------------------------
// 4. Cache-bust every changed production runtime after all earlier finalizers.
// ---------------------------------------------------------------------------
let premium = read(premiumPath);
premium = premium.replace(
  /\/final-ui-shell-v2\.js\?v=[^\"']+/g,
  "/final-ui-shell-v2.js?v=20260821-uniform-run-metrics-v1",
);
if (!premium.includes("/final-ui-shell-v2.js?v=20260821-uniform-run-metrics-v1")) {
  throw new Error("history-preload-runpanel shell cache-bust missing");
}
write(premiumPath, premium);

let index = read(indexPath);
index = index.replace(
  /\/final-premium-6f3\.js\?v=[^\"']+/g,
  "/final-premium-6f3.js?v=20260821-history-runpanel-v1",
);
index = index.replace(
  /\/direct-financial-fence-v1\.js\?v=[^\"']+/g,
  "/direct-financial-fence-v1.js?v=20260821-history-preload-v3",
);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^\"']+/g,
  "/deriv-direct-execution-v2.js?v=20260821-rolling-history-diagnostics-v1",
);
index = index.replace(
  /\/direct-transaction-ledger-v6\.js\?v=[^\"']+/g,
  "/direct-transaction-ledger-v6.js?v=20260821-uniform-metrics-v1",
);
for (const required of [
  "/final-premium-6f3.js?v=20260821-history-runpanel-v1",
  "/direct-financial-fence-v1.js?v=20260821-history-preload-v3",
  "/deriv-direct-execution-v2.js?v=20260821-rolling-history-diagnostics-v1",
  "/direct-transaction-ledger-v6.js?v=20260821-uniform-metrics-v1",
]) {
  if (!index.includes(required)) throw new Error(`history-preload-runpanel cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("History preload finalizer: Deriv prior-window hydration is mandatory before live ticks");
console.log("History preload finalizer: rolling window de-duplicates the history/live seam");
console.log("Run panel finalizer: Runs/Wins/Losses/P&L stay visible across Summary, Transactions and Journal");
console.log("Diagnostics finalizer: exact unmet condition remains visible while Auto Trading is running");
