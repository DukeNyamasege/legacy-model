import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const runPath = "dist/direct-run-panel-authority-v6.js";
const ledgerPath = "dist/direct-transaction-ledger-v6.js";

for (const path of [enginePath, runPath, ledgerPath]) {
  if (!fs.existsSync(path)) throw new Error(`runtime-safety preparation missing ${path}`);
}

let run = fs.readFileSync(runPath, "utf8").replace(/\r\n/g, "\n");

// The Run panel has two historical zero-delay renders. Make the Start-path form
// syntactically distinct so the final safety gate can patch only the Journal-tab
// render without weakening replaceOne fail-closed semantics.
const runBefore = `      state.userStopLatch = false;\n      setTimeout(queueRender, 0);\n      setTimeout(queueRender, 80);`;
const runAfter = `      state.userStopLatch = false;\n      setTimeout(() => queueRender(), 0);\n      setTimeout(queueRender, 80);`;
const runCount = run.split(runBefore).length - 1;
if (runCount === 1) {
  run = run.replace(runBefore, runAfter);
} else if (runCount === 0 && run.includes(runAfter)) {
  // Idempotent candidate rebuild.
} else {
  throw new Error(`runtime-safety preparation start-render target expected 1 match, got ${runCount}`);
}
if ((run.split("setTimeout(queueRender, 0);").length - 1) !== 1) {
  throw new Error("runtime-safety preparation did not leave exactly one Journal-tab render target");
}
fs.writeFileSync(runPath, run, "utf8");

// execution-continuity-v1 adds durable continuity diagnostics before runtime-
// coherence-v1 adds execution_ready/last_execution_error. Runtime-safety-v2 has a
// deliberately narrow insertion anchor for its no-purchase diagnostics, so put the
// readiness/error pair directly after open_contracts while retaining both continuity
// fields. This changes object-field order only; no state or behavior is removed.
let engine = fs.readFileSync(enginePath, "utf8").replace(/\r\n/g, "\n");
const engineContinuityReady = `        open_contracts: state.openContracts.size,\n        continuity_repair: true,\n        last_tick_age_ms: Math.max(0, Date.now() - Number(state.lastTickAt || Date.now())),\n        execution_ready: executionTransportReady(),\n        last_execution_error: String(state.lastExecutionError || ""),\n      };`;
const engineSafetyReady = `        open_contracts: state.openContracts.size,\n        execution_ready: executionTransportReady(),\n        last_execution_error: String(state.lastExecutionError || ""),\n        continuity_repair: true,\n        last_tick_age_ms: Math.max(0, Date.now() - Number(state.lastTickAt || Date.now())),\n      };`;
const engineContinuityCount = engine.split(engineContinuityReady).length - 1;
if (engineContinuityCount === 1) {
  engine = engine.replace(engineContinuityReady, engineSafetyReady);
} else if (engineContinuityCount === 0 && engine.includes(engineSafetyReady)) {
  // Idempotent candidate rebuild.
} else {
  throw new Error(
    `runtime-safety preparation diagnostic state export expected continuity-ready or normalized shape, got ${engineContinuityCount}`,
  );
}
for (const required of [
  "execution_ready: executionTransportReady()",
  'last_execution_error: String(state.lastExecutionError || "")',
  "continuity_repair: true",
  "last_tick_age_ms: Math.max(0, Date.now() - Number(state.lastTickAt || Date.now()))",
]) {
  if (!engine.includes(required)) throw new Error(`runtime-safety preparation state invariant missing: ${required}`);
}
fs.writeFileSync(enginePath, engine, "utf8");

// execution-continuity-v1 intentionally expands the no-row branch so an old
// server response cannot leave stale rows visible. global-recovery-v1 then
// normalizes the visible ledger header from the historical "Entry / Exit" label
// to the production "Exit digit" label. Runtime-safety-v2 takes final ownership
// immediately afterwards and replaces either known intermediate form with one
// canonical zero-row branch. Unknown shapes still fail closed.
let ledger = fs.readFileSync(ledgerPath, "utf8").replace(/\r\n/g, "\n");
const ledgerExpandedEntryExit = `    const rows = contracts();\n    if (!rows.length) {\n      const panel = document.querySelector(".global-run-panel");\n      const body = panel?.querySelector(".run-panel-body");\n      const summary = panel?.querySelector(".run-panel-stats");\n      if (body) body.innerHTML = \`<div class="transaction-table transaction-table-v6 unified-canonical-table-v10"><div class="transaction-head transaction-head-v6"><span>Time / Market</span><span>Type</span><span>Entry / Exit</span><span>Buy price</span><span>Profit / Loss</span></div><div class="transaction-rows"></div></div>\`;\n      if (summary) summary.innerHTML = statsMarkup(stats([]));\n      lastSignature = ""; connectObserver(); return;\n    }\n    const panel = document.querySelector(".global-run-panel");`;
const ledgerExpandedExitDigit = `    const rows = contracts();\n    if (!rows.length) {\n      const panel = document.querySelector(".global-run-panel");\n      const body = panel?.querySelector(".run-panel-body");\n      const summary = panel?.querySelector(".run-panel-stats");\n      if (body) body.innerHTML = \`<div class="transaction-table transaction-table-v6 unified-canonical-table-v10"><div class="transaction-head transaction-head-v6"><span>Time / Market</span><span>Type</span><span>Exit digit</span><span>Buy price</span><span>Profit / Loss</span></div><div class="transaction-rows"></div></div>\`;\n      if (summary) summary.innerHTML = statsMarkup(stats([]));\n      lastSignature = ""; connectObserver(); return;\n    }\n    const panel = document.querySelector(".global-run-panel");`;
const ledgerNormalized = `    const rows = contracts();\n    if (!rows.length) { lastSignature = ""; connectObserver(); return; }\n    const panel = document.querySelector(".global-run-panel");`;
const legacyCount = ledger.split(ledgerExpandedEntryExit).length - 1;
const exitDigitCount = ledger.split(ledgerExpandedExitDigit).length - 1;
if (exitDigitCount === 1 && legacyCount === 0) {
  ledger = ledger.replace(ledgerExpandedExitDigit, ledgerNormalized);
} else if (legacyCount === 1 && exitDigitCount === 0) {
  ledger = ledger.replace(ledgerExpandedEntryExit, ledgerNormalized);
} else if (legacyCount === 0 && exitDigitCount === 0 && ledger.includes(ledgerNormalized)) {
  // Idempotent candidate rebuild.
} else {
  throw new Error(
    `runtime-safety preparation ledger zero-row target expected exactly one supported shape, got legacy=${legacyCount} exit_digit=${exitDigitCount}`,
  );
}
if (!ledger.includes("unified-canonical-table-v10")) {
  throw new Error("runtime-safety preparation expected continuity ledger v10 shape");
}
if (ledger.includes("<span>Entry / Exit</span>")) {
  throw new Error("runtime-safety preparation legacy Entry / Exit header survived global recovery");
}
fs.writeFileSync(ledgerPath, ledger, "utf8");

console.log("Runtime safety preparation complete: final engine, Run-panel and ledger targets are unambiguous");
