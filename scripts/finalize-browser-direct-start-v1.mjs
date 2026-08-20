import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const shellPath = "dist/final-ui-shell-v2.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`browser-direct-start-v1 missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`browser-direct-start-v1 ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`browser-direct-start-v1 ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

let engine = read(enginePath);
let shell = read(shellPath);
let index = read(indexPath);

// The browser-direct v3 private close handler accidentally passed the string
// "private" into rejectPending(), whose first argument is the pending Map. That
// produces exactly: map.entries is not a function. Preserve the original API.
engine = replaceOne(
  engine,
  '          rejectPending("private", new Error(message));\n',
  '          rejectPending(state.privatePending, message);\n',
  "private pending close",
);

// Compatibility for any older shell still trying POST /me/resume-trading. Live
// manual Start is browser-owned, so the browser may never fall through to the VPS
// worker just because the six-second click-intent window expired while a strategy
// was being prepared.
engine = replaceOne(
  engine,
  '    if ((path === "/me/resume-trading" || path === "/me/auto-trade") && method === "POST" && Date.now() < state.manualIntentUntil) {\n',
  '    if ((path === "/me/resume-trading" || path === "/me/auto-trade") && method === "POST") {\n',
  "retire manual-intent expiry from live start compatibility",
);

const saveBuilder = `  async function saveBuilder({ trade = false, schedule = false, askName = false, storeLocal = askName } = {}) {\n    let snapshot = builderSnapshot();\n    if (askName && !snapshot.builder?.lockedName) snapshot = withStrategyName(snapshot, askStrategyName(snapshot.name));\n    if (askName && snapshot.builder?.lockedName) assertUniqueStrategyName(snapshot.name, snapshot.id);\n\n    // Explicit Save may persist through the control plane. Live Trade does not\n    // perform a separate custom-strategy request: the one /arm control write made\n    // by the browser engine persists the exact strategy while the financial path\n    // remains browser -> Deriv.\n    if (!trade) {\n      await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(snapshot.strategy) });\n    }\n    if (storeLocal) snapshot = saveTemplate(snapshot);\n    state.selectedStrategy = snapshot;\n    if (schedule) { go("schedule"); return; }\n    if (trade) {\n      await startBrowserDirectStrategy(snapshot.strategy);\n      go("trades");\n    }\n  }\n\n`;

shell = replaceBetween(
  shell,
  "  async function saveBuilder(",
  "  async function ensureRunnableStrategy() {",
  saveBuilder,
  "builder trade transport",
);

const runnableStart = `  async function ensureRunnableStrategy() {\n    if (state.route === "builder" && root.querySelector(".restored-builder")) {\n      const snapshot = builderSnapshot();\n      state.selectedStrategy = snapshot;\n      return snapshot;\n    }\n\n    if (state.route === "ready" && state.generated) {\n      const canonical = generatedCanonical();\n      if (!canonical) throw new Error("Generated strategy is missing its canonical execution payload.");\n      const name = state.generated.name || state.generated.strategy_name || "AI Generated Strategy";\n      const snapshot = {\n        name,\n        source: "ai",\n        strategy: canonical,\n        builder: normalizeBuilderDraft({ ...canonical, name }),\n        generated: state.generated,\n      };\n      state.selectedStrategy = snapshot;\n      return snapshot;\n    }\n\n    const selected = state.selectedStrategy || {};\n    if (selected.strategy?.market_mode) return selected;\n    return null;\n  }\n\n  async function startBrowserDirectStrategy(strategy) {\n    const direct = window.DERIVADMIN_DIRECT_EXECUTION_V1;\n    if (!direct || typeof direct.start !== "function") {\n      throw new Error("Browser-direct Deriv execution is not ready. Refresh this page and try again.");\n    }\n    const started = await direct.start(strategy || null);\n    if (!started) throw new Error("The selected strategy is not ready for browser-direct execution.");\n    return true;\n  }\n\n  async function startTradingFromContext(_mode = "continue") {\n    const selected = await ensureRunnableStrategy();\n    const strategy = selected?.strategy || selected?.canonical || selected?.config || null;\n    if (!strategy) throw new Error("Load or save a strategy before starting Auto Trading.");\n    await startBrowserDirectStrategy(strategy);\n  }\n\n`;

shell = replaceBetween(
  shell,
  "  async function ensureRunnableStrategy() {",
  "  function saveTemplate(snapshot) {",
  runnableStart,
  "manual start transport",
);

const oldScheduleTrade = `        const selected = state.selectedStrategy || strategyForSchedule();\n        if (selected.strategy?.market_mode) await json("/me/custom-strategy", { method: "POST", body: JSON.stringify(selected.strategy) });\n        await json("/me/resume-trading", { method: "POST", body: JSON.stringify({ mode: "start_again" }) }); go("trades");\n`;
const newScheduleTrade = `        const selected = state.selectedStrategy || strategyForSchedule();\n        const strategy = selected?.strategy || selected?.canonical || selected?.config || selected || null;\n        await startBrowserDirectStrategy(strategy);\n        go("trades");\n`;
shell = replaceOne(shell, oldScheduleTrade, newScheduleTrade, "trade-now browser transport");

const runState = `  function runPanelRunning() {\n    try {\n      const directRunning = window.DERIVADMIN_DIRECT_EXECUTION_V1?.state?.().running;\n      if (typeof directRunning === "boolean") return directRunning;\n    } catch (_) {}\n    const lifecycle = String(state.lifecycle?.lifecycle || state.lifecycle?.runtime_state || (state.me?.enabled ? "running" : "stopped")).toLowerCase();\n    return lifecycle.includes("running") || lifecycle.includes("active");\n  }\n\n`;
shell = replaceBetween(
  shell,
  "  function runPanelRunning() {",
  "  function runPanelStrategySource() {",
  runState,
  "direct run state authority",
);

if (shell.includes('/me/resume-trading')) {
  throw new Error("browser-direct-start-v1 live/manual shell still contains /me/resume-trading");
}
if (engine.includes('rejectPending("private"')) {
  throw new Error("browser-direct-start-v1 invalid private rejectPending call survived");
}
if (!engine.includes('rejectPending(state.privatePending, message);')) {
  throw new Error("browser-direct-start-v1 private pending Map repair missing");
}
if (!shell.includes('await startBrowserDirectStrategy(strategy);')) {
  throw new Error("browser-direct-start-v1 direct shell Start missing");
}

index = index.replaceAll(
  "deriv-direct-execution-v2.js?v=20260820-browser-deriv-direct-v3",
  "deriv-direct-execution-v2.js?v=20260820-browser-direct-start-v4",
);

write(enginePath, engine);
write(shellPath, shell);
write(indexPath, index);

console.log("BROWSER_DIRECT_START_V1_INSTALLED resume_server=false custom_strategy_start_write=false pending_map_close_fixed=true auto_trading_nonterminal_on_transport_error=true");
