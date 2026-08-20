import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const shellPath = "dist/final-ui-shell-v2.js";
const guardPath = "dist/direct-interaction-guard-v3.js";
const runPanelPath = "dist/direct-run-panel-authority-v6.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`exact-builder-live-diagnostics-v2 missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`exact-builder-live-diagnostics-v2 ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`exact-builder-live-diagnostics-v2 ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

// The preparation step owns canonical Builder review wiring. Fail closed if the
// finalized shell/guard no longer expose that canonical payload.
const shell = read(shellPath);
const guard = read(guardPath);
if (!shell.includes("function exactStrategyPreview()") || !shell.includes("exactStrategyPreview")) {
  throw new Error("canonical Builder preview is not installed in the finalized shell");
}
if (!guard.includes("exactStrategyPreview?.()")) {
  throw new Error("strategy confirmation is not using the canonical Builder preview");
}

// ---------------------------------------------------------------------------
// Browser execution: observe the exact routed evaluator without replacing the
// primary/after-loss routing semantics. `conditionMatches` remains the financial
// truth; diagnostics only explain the same result with live observed values.
// ---------------------------------------------------------------------------
let engine = read(enginePath);
if (!engine.includes("conditionDiagnostics: new Map(),")) {
  engine = replaceOne(
    engine,
    `    histories: new Map(),\n`,
    `    histories: new Map(),\n    conditionDiagnostics: new Map(),\n    privateReconnectAttempts: 0,\n    privateNextRetryAt: 0,\n    privateLastCloseCode: 0,\n    privateLastCloseAt: 0,\n`,
    "engine diagnostic state",
  );
}

const routedStrategyMarker = `  function strategyMatches(history, route = activeExecutionRoute()) {`;
if (!engine.includes("function conditionDiagnostic(condition, history)")) {
  if (!engine.includes(routedStrategyMarker)) {
    throw new Error("routed strategyMatches signature missing from finalized browser engine");
  }

  const helpers = `  function conditionDiagnostic(condition, history) {\n    const c = condition || {};\n    const n = Math.max(1, Number(c.window || 1));\n    const digits = Array.isArray(history?.digits) ? history.digits : [];\n    const quotes = Array.isArray(history?.quotes) ? history.quotes : [];\n    const target = String(c.target || "").toLowerCase();\n    const operator = String(c.operator || "");\n    const waiting = (available, required, label) => ({ label, status: "waiting", met: false, ready: false, available, required, observed: \`waiting for history \${available}/\${required}\` });\n    const result = (met, label, observed, available, required) => ({ label, status: met ? "met" : "not_met", met: Boolean(met), ready: true, available, required, observed });\n\n    if (c.kind === "digit_parity") {\n      const label = \`Last \${n} digit\${n === 1 ? "" : "s"} \${String(c.parity || "even").toLowerCase()}\`;\n      if (digits.length < n) return waiting(digits.length, n, label);\n      const sample = digits.slice(-n);\n      const met = Boolean(history?.ready) && conditionMatches(c, history);\n      return result(met, label, \`digits [\${sample.join(", ")}]\`, sample.length, n);\n    }\n\n    if (c.kind === "digit_compare") {\n      const label = ["all_same", "all_even", "all_odd"].includes(operator)\n        ? \`Last \${n} digits \${operator.replaceAll("_", " ")}\`\n        : \`Last \${n} digit\${n === 1 ? "" : "s"} \${operator || "=="} \${c.value ?? 0}\`;\n      if (digits.length < n) return waiting(digits.length, n, label);\n      const sample = digits.slice(-n);\n      const met = Boolean(history?.ready) && conditionMatches(c, history);\n      return result(met, label, \`digits [\${sample.join(", ")}]\`, sample.length, n);\n    }\n\n    if (c.kind === "direction") {\n      const direction = String(c.direction || "no_move").toLowerCase();\n      const label = \`Last \${n} quote\${n === 1 ? "" : "s"} \${direction.replaceAll("_", " ")}\`;\n      if (quotes.length < n) return waiting(quotes.length, n, label);\n      const sample = quotes.slice(-n);\n      const moves = sample.slice(1).map((quote, idx) => quote - sample[idx]);\n      const met = Boolean(history?.ready) && conditionMatches(c, history);\n      return result(met, label, \`moves [\${moves.map((move) => Number(move).toPrecision(4)).join(", ")}]\`, sample.length, n);\n    }\n\n    if (c.kind === "percentage") {\n      const labelTarget = target === "over" || target === "under"\n        ? \`\${target} \${Number(c.value)}\`\n        : target === "digit" ? \`digit \${Number(c.value)}\` : target;\n      const label = \`\${labelTarget} \${operator || ">="} \${Number(c.threshold || 0)}% in \${n} ticks\`;\n      let matches = 0;\n      let total = 0;\n      if (["rise", "fall", "no_move"].includes(target)) {\n        if (quotes.length < n) return waiting(quotes.length, n, label);\n        const sample = quotes.slice(-n);\n        const moves = sample.slice(1).map((quote, idx) => Math.sign(quote - sample[idx]));\n        total = moves.length;\n        if (target === "rise") matches = moves.filter((move) => move > 0).length;\n        else if (target === "fall") matches = moves.filter((move) => move < 0).length;\n        else matches = moves.filter((move) => move === 0).length;\n      } else {\n        if (digits.length < n) return waiting(digits.length, n, label);\n        const sample = digits.slice(-n);\n        total = sample.length;\n        if (target === "even") matches = sample.filter((digit) => digit % 2 === 0).length;\n        else if (target === "odd") matches = sample.filter((digit) => digit % 2 === 1).length;\n        else if (target === "over") matches = sample.filter((digit) => digit > Number(c.value || 0)).length;\n        else if (target === "under") matches = sample.filter((digit) => digit < Number(c.value || 0)).length;\n        else if (target === "digit") matches = sample.filter((digit) => digit === Number(c.value)).length;\n      }\n      const percentage = total ? matches * 100 / total : 0;\n      const met = Boolean(history?.ready) && conditionMatches(c, history);\n      return result(met, label, \`\${percentage.toFixed(2)}% (\${matches}/\${total})\`, total, n);\n    }\n\n    return { label: String(c.kind || "unknown condition"), status: "not_met", met: false, ready: true, available: 0, required: 0, observed: "unsupported condition" };\n  }\n\n`;
  engine = engine.replace(routedStrategyMarker, helpers + routedStrategyMarker);
}

const routedStrategyOld = `  function strategyMatches(history, route = activeExecutionRoute()) {\n    if (!route || !history?.ready) return false;\n    return route.conditions.every((condition) => conditionMatches(condition, history));\n  }`;
const routedStrategyNew = `  function strategyMatches(history, route = activeExecutionRoute(), symbol = "") {\n    if (!route) return false;\n    const conditions = Array.isArray(route.conditions) ? route.conditions : [];\n    const rows = conditions.map((condition) => conditionDiagnostic(condition, history));\n    const ready = Boolean(history?.ready) && rows.every((row) => row.ready);\n    const met = ready && conditions.every((condition) => conditionMatches(condition, history));\n    if (symbol) state.conditionDiagnostics.set(symbol, {\n      symbol,\n      route_key: String(route.route_key || "primary"),\n      at: new Date().toISOString(),\n      ready,\n      met,\n      conditions: rows,\n    });\n    return met;\n  }`;
engine = replaceOne(engine, routedStrategyOld, routedStrategyNew, "routed strategy diagnostic wrapper");
engine = replaceOne(
  engine,
  `    if (!strategyMatches(history, route)) {`,
  `    if (!strategyMatches(history, route, symbol)) {`,
  "symbol-aware routed strategy evaluation",
);

// Current connection state, not a historical 1006 string, owns diagnostics.
if (!engine.includes("state.privateReconnectAttempts += 1;")) {
  engine = replaceOne(
    engine,
    `          state.lastExecutionError = message;\n          rejectPending(state.privatePending, message);`,
    `          state.lastExecutionError = message;\n          state.privateReconnectAttempts += 1;\n          state.privateLastCloseCode = code;\n          state.privateLastCloseAt = Date.now();\n          rejectPending(state.privatePending, message);`,
    "private close diagnostics",
  );
  engine = replaceOne(
    engine,
    `            const delay = Math.max(1000, Math.min(15000, Number(state.directPrivateRetryMs || 1000)));\n            state.directPrivateRetryMs = Math.min(15000, Math.round(delay * 1.7));`,
    `            const delay = Math.max(1000, Math.min(15000, Number(state.directPrivateRetryMs || 1000)));\n            state.privateNextRetryAt = Date.now() + delay;\n            state.directPrivateRetryMs = Math.min(15000, Math.round(delay * 1.7));`,
    "private retry timestamp",
  );
  engine = replaceOne(
    engine,
    `          state.directPrivateRetryMs = 1000;\n          if (state.running && !state.ownerLost) {`,
    `          state.directPrivateRetryMs = 1000;\n          state.privateReconnectAttempts = 0;\n          state.privateNextRetryAt = 0;\n          state.privateLastCloseCode = 0;\n          if (state.running && !state.ownerLost) {`,
    "private recovery reset",
  );
}

if (!engine.includes("diagnostics() {")) {
  engine = replaceOne(
    engine,
    `    prewarm: prewarmData,\n    state() {`,
    `    prewarm: prewarmData,\n    diagnostics() {\n      const conditionDiagnostics = {};\n      for (const [symbol, row] of state.conditionDiagnostics.entries()) {\n        conditionDiagnostics[symbol] = JSON.parse(JSON.stringify(row));\n      }\n      return {\n        at: new Date().toISOString(),\n        running: Boolean(state.running),\n        armed: Boolean(state.armed),\n        owner_lost: Boolean(state.ownerLost),\n        in_flight: Boolean(state.inFlight),\n        public_ready: state.publicWs?.readyState === WebSocket.OPEN,\n        private_ready: state.privateWs?.readyState === WebSocket.OPEN,\n        public_ready_state: Number(state.publicWs?.readyState ?? -1),\n        private_ready_state: Number(state.privateWs?.readyState ?? -1),\n        last_execution_error: String(state.lastExecutionError || ""),\n        private_reconnect_attempts: Number(state.privateReconnectAttempts || 0),\n        private_next_retry_at: Number(state.privateNextRetryAt || 0),\n        private_last_close_code: Number(state.privateLastCloseCode || 0),\n        private_last_close_at: Number(state.privateLastCloseAt || 0),\n        active_route: activeExecutionRoute(),\n        strategy: state.strategy ? JSON.parse(JSON.stringify(state.strategy)) : null,\n        conditions: conditionDiagnostics,\n      };\n    },\n    state() {`,
    "public diagnostics API",
  );
}

// Replace the old 60-second reason with a current-state diagnosis. A recovered
// socket makes old 1006 text historical and therefore non-blocking.
const exactReason = `  function exactRuntimeDiagnostics() {\n    try { return window.DERIVADMIN_DIRECT_EXECUTION_V1?.diagnostics?.() || {}; }\n    catch (_) { return {}; }\n  }\n\n  function firstConditionBlocker(diagnostics) {\n    const markets = Object.values(diagnostics?.conditions || {});\n    const waitingMarket = markets.find((market) => Array.isArray(market?.conditions) && market.conditions.some((row) => row?.status === "waiting"));\n    if (waitingMarket) {\n      const row = waitingMarket.conditions.find((item) => item?.status === "waiting");\n      return \`Waiting for exact Builder history on \${waitingMarket.symbol}: \${row.label} — \${row.observed}.\`;\n    }\n    const blocked = markets.find((market) => market?.ready && market?.met === false && Array.isArray(market?.conditions));\n    if (blocked) {\n      const row = blocked.conditions.find((item) => item?.status === "not_met");\n      if (row) return \`Exact Builder condition not met on \${blocked.symbol}: \${row.label} — observed \${row.observed}.\`;\n    }\n    return "";\n  }\n\n  function noPurchaseReason() {\n    const diagnostics = exactRuntimeDiagnostics();\n    const financial = window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1?.state?.() || {};\n    if (state.ownerLost) return "Auto Trading is ON; browser execution ownership is recovering.";\n    if (!state.armed) return "Auto Trading is ON; Start control synchronization is retrying. Browser execution is not armed yet.";\n    if (diagnostics.public_ready !== true) return "Auto Trading is ON; browser market-data WebSocket is reconnecting directly to Deriv.";\n    if (diagnostics.private_ready !== true) {\n      const code = Number(diagnostics.private_last_close_code || 0);\n      const attempts = Number(diagnostics.private_reconnect_attempts || 0);\n      const retryAt = Number(diagnostics.private_next_retry_at || 0);\n      const waitMs = retryAt > Date.now() ? retryAt - Date.now() : 0;\n      const codeText = code ? \` code \${code}\` : "";\n      const attemptText = attempts ? \`reconnect attempt \${attempts}\` : "reconnecting";\n      const waitText = waitMs > 0 ? \` in ~\${Math.ceil(waitMs / 1000)}s\` : " now";\n      return \`Auto Trading is ON; Direct Deriv trade WebSocket is disconnected\${codeText}; browser \${attemptText}\${waitText}.\`;\n    }\n    if (financial.buy_allowed === false) return "Auto Trading is ON; the browser BUY fence is temporarily not ready and is recovering.";\n    if (state.inFlight || diagnostics.in_flight) return "Exact Builder conditions qualified and a browser-direct Deriv proposal/BUY request is currently in flight.";\n    const blocker = firstConditionBlocker(diagnostics);\n    if (blocker) return blocker;\n    return state.lastBlockingReason || "Exact Builder conditions are not currently met; waiting for a qualifying live tick.";\n  }\n\n`;
engine = replaceBetween(engine, `  function noPurchaseReason() {`, `  function publishNoPurchaseDiagnostic() {`, exactReason, "exact no-purchase reason");
write(enginePath, engine);

// ---------------------------------------------------------------------------
// Cross-device synchronization: generic server enabled=false/stopped ownership is
// advisory in browser-direct mode. Only durable hard_stop or explicit TP/SL/manual
// terminal status may stop the local browser engine.
// ---------------------------------------------------------------------------
let runPanel = read(runPanelPath);
runPanel = replaceOne(
  runPanel,
  `    const remoteStopped = Boolean(\n      payload?.hard_stop\n      || payload?.enabled === false\n      || live.status === "stopped"\n      || live.status === "hard_stopped"\n      || live.owner === "stopped"\n    );`,
  `    const terminalStatus = [\n      "hard_stopped",\n      "stopped_manual",\n      "manual_stop",\n      "stopped_take_profit",\n      "take_profit",\n      "stopped_stop_loss",\n      "stop_loss",\n    ].some((token) => live.status === token || live.status.includes(token));\n    const remoteStopped = Boolean(payload?.hard_stop === true || terminalStatus);`,
  "terminal-only remote stop",
);
write(runPanelPath, runPanel);

// Cache-bust both the live engine and confirmation authority.
let index = read(indexPath);
index = index.replace(/deriv-direct-execution-v2\.js\?v=[^"']+/g, "deriv-direct-execution-v2.js?v=20260821-exact-builder-diagnostics-v6");
index = index.replace(/direct-interaction-guard-v3\.js\?v=[^"']+/g, "direct-interaction-guard-v3.js?v=20260821-exact-builder-review-v6");
write(indexPath, index);

// Final fail-closed release assertions.
const builtEngine = read(enginePath);
const builtRunPanel = read(runPanelPath);
const builtIndex = read(indexPath);
for (const marker of [
  "conditionDiagnostics: new Map()",
  "function conditionDiagnostic(condition, history)",
  "strategyMatches(history, route = activeExecutionRoute(), symbol = \"\")",
  "diagnostics() {",
  "private_reconnect_attempts",
  "Waiting for exact Builder history",
  "Exact Builder condition not met",
  "Auto Trading is ON; Direct Deriv trade WebSocket is disconnected",
]) {
  if (!builtEngine.includes(marker)) throw new Error(`engine exact diagnostic marker missing: ${marker}`);
}
if (builtRunPanel.includes("payload?.enabled === false")) throw new Error("stale enabled=false can still stop browser execution");
if (!builtRunPanel.includes("payload?.hard_stop === true || terminalStatus")) throw new Error("terminal-only remote stop authority missing");
if (!builtIndex.includes("20260821-exact-builder-diagnostics-v6")) throw new Error("exact diagnostics release key missing from index");
if (!builtIndex.includes("20260821-exact-builder-review-v6")) throw new Error("exact Builder review release key missing from index");

console.log("EXACT_BUILDER_LIVE_DIAGNOSTICS_V2_INSTALLED canonical_review=true routed_conditions=true current_transport=true terminal_stop_only=true auto_trade_recoverable=true");
