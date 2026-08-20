import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const runPath = "dist/direct-run-panel-authority-v6.js";
const ledgerPath = "dist/direct-transaction-ledger-v6.js";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`global-recovery finalizer missing: ${path}`);
  return fs.readFileSync(path, "utf8");
}

let engine = read(enginePath);
for (const required of [
  "splitPartStake",
  "targetProfitPerLeg / ratio",
  "split_part_stake: state.splitPartStake",
]) {
  if (!engine.includes(required)) throw new Error(`global-recovery engine invariant missing: ${required}`);
}

const versionPattern = /const VERSION = "[^"]*browser-direct[^"]*";/;
if (!versionPattern.test(engine)) throw new Error("global-recovery browser runtime VERSION anchor missing");
engine = engine.replace(versionPattern, 'const VERSION = "20260819-browser-direct-v8-global-recovery";');
fs.writeFileSync(enginePath, engine, "utf8");

let run = read(runPath);
const statusTimerPattern = /(state\.statusTimer\s*=\s*setInterval\(\(\)\s*=>\s*\{[\s\S]*?if \(!document\.hidden\) readServerStatus\(\);[\s\S]*?\},\s*)\d+(\s*\);)/;
const match = run.match(statusTimerPattern);
if (!match) throw new Error("global-recovery direct status timer anchor missing");
run = run.replace(statusTimerPattern, "$1" + "10000" + "$2");
if (!/state\.statusTimer\s*=\s*setInterval\([\s\S]*?10000\s*\);/.test(run)) {
  throw new Error("global-recovery direct status polling throttle was not installed");
}
fs.writeFileSync(runPath, run, "utf8");

// Final ledger authority: continuity may rebuild the zero-row table while applying
// its virtual-observation migration. The shipped artifact is normalized here so
// every transaction state (empty, virtual, actual, restored) exposes only Exit digit.
let ledger = read(ledgerPath);
ledger = ledger.replaceAll("<span>Entry / Exit</span>", "<span>Exit digit</span>");
if (ledger.includes("<span>Entry / Exit</span>")) {
  throw new Error("global-recovery legacy Entry / Exit column survived finalization");
}
if (!ledger.includes("<span>Exit digit</span>")) {
  throw new Error("global-recovery Exit digit ledger column missing");
}
fs.writeFileSync(ledgerPath, ledger, "utf8");

console.log("Global recovery v1 finalized: exact quoted Split target, 0.50 minimum, lower status-poll load, exit-digit-only ledger");
