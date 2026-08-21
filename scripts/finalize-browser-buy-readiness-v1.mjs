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

// The financial fence owns account/browser execution ownership only. Market
// history readiness belongs to the per-symbol strategy evaluator. A global
// hydration counter must never freeze BUY for every market/account because one
// history request is retrying.
let fence = read(fencePath);
fence = replaceOne(
  fence,
  `  function leaseAllowsBuy() {\n    if (!state.armed || !state.epoch || !state.lastAckAt || state.hydrationPending > 0) return false;`,
  `  function leaseAllowsBuy() {\n    if (!state.armed || !state.epoch || !state.lastAckAt) return false;`,
  "remove global history hydration from financial ownership gate",
);

fence = replaceOne(
  fence,
  `    state: () => ({ ...state, buy_allowed: leaseAllowsBuy() }),`,
  `    state: () => ({\n      ...state,\n      buy_allowed: leaseAllowsBuy(),\n      ownership_ready: Boolean(state.armed && state.epoch && state.lastAckAt),\n      history_pending: Math.max(0, Number(state.hydrationPending || 0)),\n    }),`,
  "export independent ownership and history readiness",
);

fence = fence.replace(
  `version: "20260821-direct-financial-fence-v3-history-preload"`,
  `version: "20260821-direct-financial-fence-v4-independent-history"`,
);

for (const required of [
  "if (!state.armed || !state.epoch || !state.lastAckAt) return false;",
  "ownership_ready:",
  "history_pending:",
  "direct-financial-fence-v4-independent-history",
]) {
  if (!fence.includes(required)) throw new Error(`browser-buy-readiness fence invariant missing: ${required}`);
}
if (fence.includes("state.hydrationPending > 0) return false")) {
  throw new Error("browser-buy-readiness global hydration BUY lock survived");
}
write(fencePath, fence);

// Improve the 60-second diagnosis: if history is pending, report history. If the
// financial ownership lease itself is recovering, report ownership. Never label a
// normal not-met strategy as a BUY-fence failure.
let engine = read(enginePath);
engine = replaceOne(
  engine,
  `    if (financial.buy_allowed === false) return "Auto Trading is ON; the browser BUY fence is temporarily not ready and is recovering.";`,
  `    if (financial.buy_allowed === false) {\n      return "Auto Trading is ON; browser financial ownership is not ready yet; the execution lease heartbeat is recovering.";\n    }\n    if (Number(financial.history_pending || financial.hydrationPending || 0) > 0) {\n      const blocker = firstConditionBlocker(diagnostics);\n      if (blocker) return blocker;\n      return "Auto Trading is ON; loading the required previous Deriv ticks before this market can qualify.";\n    }`,
  "separate financial ownership from market history diagnosis",
);
for (const required of [
  "browser financial ownership is not ready yet",
  "loading the required previous Deriv ticks",
  "financial.history_pending || financial.hydrationPending",
]) {
  if (!engine.includes(required)) throw new Error(`browser-buy-readiness engine invariant missing: ${required}`);
}
write(enginePath, engine);

let index = read(indexPath);
index = index.replace(
  /\/direct-financial-fence-v1\.js\?v=[^"']+/g,
  "/direct-financial-fence-v1.js?v=20260821-buy-readiness-v4",
);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260821-buy-readiness-diagnostics-v2",
);
for (const required of [
  "/direct-financial-fence-v1.js?v=20260821-buy-readiness-v4",
  "/deriv-direct-execution-v2.js?v=20260821-buy-readiness-diagnostics-v2",
]) {
  if (!index.includes(required)) throw new Error(`browser-buy-readiness cache invariant missing: ${required}`);
}
write(indexPath, index);

console.log("BUY readiness finalizer: account ownership no longer depends on global history hydration");
console.log("BUY readiness finalizer: each market remains unable to qualify until its own required Deriv history is ready");
console.log("BUY readiness finalizer: 60-second diagnostics distinguish ownership, history and unmet strategy conditions");
