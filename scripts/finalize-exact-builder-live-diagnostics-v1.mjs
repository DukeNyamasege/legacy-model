import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const shellPath = "dist/final-ui-shell-v2.js";
const guardPath = "dist/direct-interaction-guard-v3.js";
const runPanelPath = "dist/direct-run-panel-authority-v6.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`exact-builder-live-diagnostics missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`exact-builder-live-diagnostics ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`exact-builder-live-diagnostics ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

// One canonical Builder payload powers both confirmation UI and execution.
let shell = read(shellPath);
if (!shell.includes("function exactStrategyPreview()")) {
  shell = replaceOne(
    shell,
    `  window.FOA_FINAL_UI = Object.freeze({ version: "6f2", render, refresh, go });`,
    `  function exactStrategyPreview() {\n    try {\n      if (state.route === "builder" && root.querySelector(".restored-builder")) return builderSnapshot();\n      if (state.route === "ready" && state.generated) {\n        const canonical = generatedCanonical();\n        if (canonical) {\n          return {\n            name: state.generated.name || state.generated.strategy_name || canonical.name || "AI Generated Strategy",\n            source: "ai",\n            strategy: canonical,\n          };\n        }\n      }\n      if (state.selectedStrategy?.strategy?.market_mode) return state.selectedStrategy;\n      if (state.custom?.config?.configured) {\n        return { name: state.custom.config.name || state.custom.config.strategy_name || "Saved strategy", source: "server", strategy: state.custom.config };\n      }\n    } catch (_) {}\n    return null;\n  }\n\n  window.FOA_FINAL_UI = Object.freeze({ version: "6f2", render, refresh, go, exactStrategyPreview });`,
    "shell exact strategy preview",
  );
}
write(shellPath, shell);

let guard = read(guardPath);
if (!guard.includes("exactStrategyPreview?.()")) {
  guard = replaceOne(
    guard,
    `  function savedSummary() {\n    const strategy = runtime().strategy || {};`,
    `  function savedSummary(strategyOverride = null, nameOverride = "") {\n    const strategy = strategyOverride || runtime().strategy || {};`,
    "guard saved summary override",
  );
  guard = replaceOne(
    guard,
    `    const name = String(strategy.name || strategy.strategy_name || "Current strategy");`,
    `    const name = String(nameOverride || strategy.name || strategy.strategy_name || "Current strategy");`,
    "guard exact name override",
  );
  guard = replaceBetween(
    guard,
    `  function summaryFor(target) {`,
    `  function focusableElements(overlay) {`,
    `  function summaryFor(target) {\n    try {\n      const exact = window.FOA_FINAL_UI?.exactStrategyPreview?.();\n      const exactStrategy = exact?.strategy || exact?.canonical || exact?.config || null;\n      if (exactStrategy?.market_mode) return savedSummary(exactStrategy, exact?.name || "");\n    } catch (_) {}\n\n    // DOM parsing remains only as a compatibility fallback for older shells.\n    if (\n      target.closest(".builder-panel")\n      || target.closest(".builder-workspace")\n      || target.hasAttribute("data-builder-trade")\n    ) return builderSummary(target);\n    return savedSummary();\n  }\n\n`,
    "guard exact summary authority",
  );
}
write(guardPath, guard);

// Exact live condition observations and current transport diagnostics.
let engine = read(enginePath);
if (!engine.includes("conditionDiagnostics: new Map(),")) {
  engine = replaceOne(
    engine,
    `    histories: new Map(),\n`,
    `    histories: new Map(),\n    conditionDiagnostics: new Map(),\n    privateReconnectAttempts: 0,\n    privateNextRetryAt: 0,\n    privateLastCloseCode: 0,\n    privateLastCloseAt: 0,\n`,
    "engine diagnostic state",
  );
}

const exactEvaluator = `  function conditionDiagnostic(condition, history) {\n    const c = condition || {};\n    const n = Math.max(1, Number(c.window || 1));\n    const digits = history.digits || [];\n    const quotes = history.quotes || [];\n    const target = String(c.target || "").toLowerCase();\n    const operator = String(c.operator || "");\n    const waiting = (available, required, label) => ({ label, status: "waiting", met: false, ready: false, available, required, observed: \`waiting for history \${available}/\${required}\` });\n    const result = (met, label, observed, available, required) => ({ label, status: met ? "met" : "not_met", met: Boolean(met), ready: true, available, required, observed });\n\n    if (c.kind === "digit_parity") {\n      const label = \`Last \${n} digit\${n === 1 ? "" : "s"} \${String(c.parity || "even").toLowerCase()}\`;\n      if (digits.length < n) return waiting(digits.length, n, label);\n      const sample = digits.slice(-n);\n      const even = String(c.parity || "even").toLowerCase() === "even";\n      return result(sample.every((digit) => (digit % 2 === 0) === even), label, \`digits [\${sample.join(", ")}]\`, sample.length, n);\n    }\n\n    if (c.kind === "digit_compare") {\n      let label;\n      if (["all_same", "all_even", "all_odd"].includes(operator)) label = \`Last \${n} digits \${operator.replaceAll("_", " ")}\`;\n      else label = \`Last \${n} digit\${n === 1 ? "" : "s"} \${operator || "=="} \${c.value ?? 0}\`;\n      if (digits.length < n) return waiting(digits.length, n, label);\n      const sample = digits.slice(-n);\n      let met = false;\n      if (operator === "all_same") met = sample.every((digit) => digit === sample[0]);\n      else if (operator === "all_even") met = sample.every((digit) => digit % 2 === 0);\n      else if (operator === "all_odd") met = sample.every((digit) => digit % 2 === 1);\n      else met = sample.every((digit) => compare(digit, operator, Number(c.value)));\n      return result(met, label, \`digits [\${sample.join(", ")}]\`, sample.length, n);\n    }\n\n    if (c.kind === "direction") {\n      const direction = String(c.direction || "no_move").toLowerCase();\n      const label = \`Last \${n} move\${n === 1 ? "" : "s"} \${direction.replaceAll("_", " ")}\`;\n      if (quotes.length < n + 1) return waiting(Math.max(0, quotes.length - 1), n, label);\n      const sample = quotes.slice(-(n + 1));\n      const moves = sample.slice(1).map((quote, idx) => quote - sample[idx]);\n      let met = false;\n      if (["rise", "rising"].includes(direction)) met = moves.every((move) => move > 0);\n      else if (["fall", "falling"].includes(direction)) met = moves.every((move) => move < 0);\n      else met = moves.every((move) => move === 0);\n      return result(met, label, \`moves [\${moves.map((move) => Number(move).toPrecision(4)).join(", ")}]\`, moves.length, n);\n    }\n\n    if (c.kind === "percentage") {\n      const labelTarget = target === "over" || target === "under" ? \`\${target} \${Number(c.value)}\` : target === "digit" ? \`digit \${Number(c.value)}\` : target;\n      const label = \`\${labelTarget} \${operator || ">="} \${Number(c.threshold || 0)}% in \${n} ticks\`;\n      let matches = 0;\n      let total = 0;\n      if (["rise", "fall", "no_move"].includes(target)) {\n        if (quotes.length < n + 1) return waiting(Math.max(0, quotes.length - 1), n, label);\n        const sample = quotes.slice(-(n + 1));\n        const moves = sample.slice(1).map((quote, idx) => quote - sample[idx]);\n        total = moves.length;\n        if (target === "rise") matches = moves.filter((move) => move > 0).length;\n        else if (target === "fall") matches = moves.filter((move) => move < 0).length;\n        else matches = moves.filter((move) => move === 0).length;\n      } else {\n        if (digits.length < n) return waiting(digits.length, n, label);\n        const sample = digits.slice(-n);\n        total = sample.length;\n        if (target === "even") matches = sample.filter((digit) => digit % 2 === 0).length;\n        else if (target === "odd") matches = sample.filter((digit) => digit % 2 === 1).length;\n        else if (target === "over") matches = sample.filter((digit) => digit > Number(c.value)).length;\n        else if (target === "under") matches = sample.filter((digit) => digit < Number(c.value)).length;\n        else if (target === "digit") matches = sample.filter((digit) => digit === Number(c.value)).length;\n      }\n      const percentage = total ? matches * 100 / total : 0;\n      const met = total > 0 && compare(percentage, operator, Number(c.threshold || 0));\n      return result(met, label, \`\${percentage.toFixed(2)}% (\${matches}/\${total})\`, total, n);\n    }\n\n    return { label: String(c.kind || "unknown condition"), status: "not_met", met: false, ready: true, available: 0, required: 0, observed: "unsupported condition" };\n  }\n\n  function strategyMatches(history, symbol = "") {\n    const strategy = state.strategy;\n    if (!strategy) return false;\n    const conditions = Array.isArray(strategy.conditions) ? strategy.conditions : [];\n    if (!conditions.length) {\n      if (symbol) state.conditionDiagnostics.set(symbol, { symbol, at: new Date().toISOString(), ready: true, met: true, conditions: [] });\n      return true;\n    }\n    const rows = conditions.map((condition) => conditionDiagnostic(condition, history));\n    const ready = rows.every((row) => row.ready);\n    const met = ready && rows.every((row) => row.met);\n    if (symbol) state.conditionDiagnostics.set(symbol, { symbol, at: new Date().toISOString(), ready, met, conditions: rows });\n    return met;\n  }\n\n`;
engine = replaceBetween(engine, "  function strategyMatches(history) {", "  function settleVirtual(history, pending) {", exactEvaluator, "engine exact condition evaluator");
engine = replaceOne(engine, `    const met = strategyMatches(history);`, `    const met = strategyMatches(history, route);`, "engine symbol-aware strategy evaluation");

if (!engine.includes("state.privateReconnectAttempts += 1;")) {
  engine = replaceOne(
    engine,
    `          state.lastExecutionError = message;\n          rejectPending(state.privatePending, message);`,
    `          state.lastExecutionError = message;\n          state.privateReconnectAttempts += 1;\n          state.privateLastCloseCode = code;\n          state.privateLastCloseAt = Date.now();\n          rejectPending(state.privatePending, message);`,
    "engine private close diagnostics",
  );
  engine = replaceOne(
    engine,
    `            const delay = Math.max(1000, Math.min(15000, Number(state.directPrivateRetryMs || 1000)));\n            state.directPrivateRetryMs = Math.min(15000, Math.round(delay * 1.7));`,
    `            const delay = Math.max(1000, Math.min(15000, Number(state.directPrivateRetryMs || 1000)));\n            state.privateNextRetryAt = Date.now() + delay;\n            state.directPrivateRetryMs = Math.min(15000, Math.round(delay * 1.7));`,
    "engine private retry timestamp",
  );
  engine = replaceOne(
    engine,
    `          state.directPrivateRetryMs = 1000;\n          if (state.running && !state.ownerLost) {`,
    `          state.directPrivateRetryMs = 1000;\n          state.privateReconnectAttempts = 0;\n          state.privateNextRetryAt = 0;\n          state.privateLastCloseCode = 0;\n          if (state.running && !state.ownerLost) {`,
    "engine private recovery reset",
  );
}

if (!engine.includes("diagnostics() {")) {
  engine = replaceOne(
    engine,
    `    prewarm: prewarmData,\n    state() {`,
    `    prewarm: prewarmData,\n    diagnostics() {\n      const conditionDiagnostics = {};\n      for (const [symbol, row] of state.conditionDiagnostics.entries()) conditionDiagnostics[symbol] = JSON.parse(JSON.stringify(row));\n      return {\n        at: new Date().toISOString(),\n        running: Boolean(state.running),\n        armed: Boolean(state.armed),\n        owner_lost: Boolean(state.ownerLost),\n        in_flight: Boolean(state.inFlight),\n        public_ready: state.publicWs?.readyState === WebSocket.OPEN,\n        private_ready: state.privateWs?.readyState === WebSocket.OPEN,\n        public_ready_state: Number(state.publicWs?.readyState ?? -1),\n        private_ready_state: Number(state.privateWs?.readyState ?? -1),\n        last_execution_error: String(state.lastExecutionError || ""),\n        private_reconnect_attempts: Number(state.privateReconnectAttempts || 0),\n        private_next_retry_at: Number(state.privateNextRetryAt || 0),\n        private_last_close_code: Number(state.privateLastCloseCode || 0),\n        private_last_close_at: Number(state.privateLastCloseAt || 0),\n        strategy: state.strategy ? JSON.parse(JSON.stringify(state.strategy)) : null,\n        conditions: conditionDiagnostics,\n      };\n    },\n    state() {`,
    "engine public diagnostics API",
  );
}
write(enginePath, engine);

// Generic enabled=false/stale server state cannot stop browser-direct execution.
let runPanel = read(runPanelPath);
runPanel = replaceOne(
  runPanel,
  `    const remoteStopped = Boolean(\n      payload?.hard_stop\n      || payload?.enabled === false\n      || live.status === "stopped"\n      || live.status === "hard_stopped"\n      || live.owner === "stopped"\n    );`,
  `    const terminalStatus = [\n      "hard_stopped",\n      "stopped_manual",\n      "manual_stop",\n      "stopped_take_profit",\n      "take_profit",\n      "stopped_stop_loss",\n      "stop_loss",\n    ].some((token) => live.status === token || live.status.includes(token));\n    const remoteStopped = Boolean(payload?.hard_stop === true || terminalStatus);`,
  "terminal-only remote stop",
);

const exactNoPurchase = `  function exactRuntimeDiagnostics() {\n    try { return window.DERIVADMIN_DIRECT_EXECUTION_V1?.diagnostics?.() || {}; }\n    catch (_) { return {}; }\n  }\n\n  function firstConditionBlocker(diagnostics) {\n    const markets = Object.values(diagnostics?.conditions || {});\n    const waitingMarket = markets.find((market) => Array.isArray(market?.conditions) && market.conditions.some((row) => row?.status === "waiting"));\n    if (waitingMarket) {\n      const row = waitingMarket.conditions.find((item) => item?.status === "waiting");\n      return \`Waiting for exact Builder history on \${waitingMarket.symbol}: \${row.label} — \${row.observed}.\`;\n    }\n    const blocked = markets.find((market) => market?.ready && market?.met === false && Array.isArray(market?.conditions));\n    if (blocked) {\n      const row = blocked.conditions.find((item) => item?.status === "not_met");\n      if (row) return \`Exact Builder condition not met on \${blocked.symbol}: \${row.label} — observed \${row.observed}.\`;\n    }\n    return "";\n  }\n\n  function noPurchaseReason(snapshot) {\n    const diagnostics = exactRuntimeDiagnostics();\n    const privateOpen = Boolean(diagnostics.private_ready ?? snapshot.private_ready)\n      || Number(diagnostics.private_ready_state ?? snapshot.private_ready_state) === WebSocket.OPEN;\n    const publicOpen = Boolean(diagnostics.public_ready ?? snapshot.public_ready)\n      || Number(diagnostics.public_ready_state ?? snapshot.public_ready_state) === WebSocket.OPEN;\n    if (snapshot.owner_lost) return "Browser execution ownership is unavailable; Start synchronization is retrying without stopping Auto Trading.";\n    if (!snapshot.armed) return "Auto Trading is ON; Start control synchronization is retrying. Browser execution has not been armed yet.";\n    if (!publicOpen) return "Auto Trading is ON; browser market-data WebSocket is reconnecting directly to Deriv.";\n    if (!privateOpen) {\n      const code = Number(diagnostics.private_last_close_code || 0);\n      const attempts = Number(diagnostics.private_reconnect_attempts || 0);\n      const retryAt = Number(diagnostics.private_next_retry_at || 0);\n      const waitMs = retryAt > Date.now() ? retryAt - Date.now() : 0;\n      const error = String(diagnostics.last_execution_error || snapshot.last_execution_error || "");\n      const codeText = code ? \` code \${code}\` : "";\n      const attemptText = attempts ? \`reconnect attempt \${attempts}\` : "reconnecting";\n      const waitText = waitMs > 0 ? \` in ~\${Math.ceil(waitMs / 1000)}s\` : " now";\n      const detail = error ? \` Last transport error: \${shortText(error, 170)}.\` : "";\n      return \`Auto Trading is ON; Direct Deriv trade WebSocket is disconnected\${codeText}; browser \${attemptText}\${waitText}.\${detail}\`;\n    }\n    if (snapshot.in_flight || diagnostics.in_flight) return "Exact Builder conditions qualified and a browser-direct Deriv proposal/BUY request is currently in flight.";\n    const blocker = firstConditionBlocker(diagnostics);\n    if (blocker) return blocker;\n    const live = latestLiveSnapshot();\n    if (live && live.met === false) {\n      const market = shortText(live.market || "", 20);\n      return \`Exact Builder conditions are not currently met\${market ? \` on \${market}\` : ""}.\`;\n    }\n    return \`No purchase confirmation yet after \${Math.max(60, Math.floor((Date.now() - Number(state.runStartedAt || Date.now())) / 1000))} seconds; browser-direct execution remains active.\`;\n  }\n\n`;
runPanel = replaceBetween(runPanel, `  function noPurchaseReason(snapshot) {`, `  function publishNoPurchaseDiagnostic() {`, exactNoPurchase, "exact no-purchase diagnostics");
if (runPanel.includes("payload?.enabled === false")) throw new Error("generic enabled=false remote stop survived exact diagnostics finalizer");
write(runPanelPath, runPanel);

let index = read(indexPath);
index = index.replaceAll("deriv-direct-execution-v2.js?v=20260820-browser-direct-start-v4", "deriv-direct-execution-v2.js?v=20260820-exact-builder-diagnostics-v5");
index = index.replaceAll("direct-interaction-guard-v3.js?v=20260818-interaction-v4-one-flow", "direct-interaction-guard-v3.js?v=20260820-exact-builder-review-v5");
write(indexPath, index);

const builtEngine = read(enginePath);
const builtShell = read(shellPath);
const builtGuard = read(guardPath);
const builtRunPanel = read(runPanelPath);
const builtIndex = read(indexPath);
for (const marker of ["conditionDiagnostics: new Map()", "function conditionDiagnostic(condition, history)", "diagnostics() {", "private_reconnect_attempts", "strategyMatches(history, route)"]) {
  if (!builtEngine.includes(marker)) throw new Error(`engine exact diagnostic marker missing: ${marker}`);
}
for (const marker of ["function exactStrategyPreview()", "exactStrategyPreview });"]) {
  if (!builtShell.includes(marker)) throw new Error(`shell exact strategy marker missing: ${marker}`);
}
if (!builtGuard.includes("exactStrategyPreview?.()")) throw new Error("confirmation dialog is not using canonical Builder strategy");
if (builtRunPanel.includes("payload?.enabled === false")) throw new Error("stale enabled=false can still stop browser execution");
for (const marker of ["Waiting for exact Builder history", "Exact Builder condition not met", "Auto Trading is ON; Direct Deriv trade WebSocket is disconnected"]) {
  if (!builtRunPanel.includes(marker)) throw new Error(`run-panel exact diagnostic marker missing: ${marker}`);
}
if (!builtIndex.includes("20260820-exact-builder-diagnostics-v5")) throw new Error("exact diagnostics release key missing from index");

console.log("EXACT_BUILDER_LIVE_DIAGNOSTICS_V1_INSTALLED canonical_review=true exact_conditions=true terminal_stop_only=true auto_trade_recoverable=true");
