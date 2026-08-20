import fs from "node:fs";

const runPath = "dist/direct-run-panel-authority-v6.js";
const ledgerPath = "dist/direct-transaction-ledger-v6.js";

for (const path of [runPath, ledgerPath]) {
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

console.log("Runtime safety preparation complete: final Run-panel and ledger targets are unambiguous");
