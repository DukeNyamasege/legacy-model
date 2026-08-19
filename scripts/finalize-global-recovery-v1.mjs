import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const runPath = "dist/direct-run-panel-authority-v6.js";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`global-recovery finalizer missing: ${path}`);
  return fs.readFileSync(path, "utf8");
}

function replaceOnce(text, before, after, label) {
  const count = text.split(before).length - 1;
  if (count !== 1) throw new Error(`global-recovery ${label}: expected one source match, got ${count}`);
  return text.replace(before, after);
}

let engine = read(enginePath);
for (const required of [
  "splitPartStake",
  "Math.max(0.50, fullOneShotStake / parts)",
  "split_part_stake: state.splitPartStake",
]) {
  if (!engine.includes(required)) throw new Error(`global-recovery engine invariant missing: ${required}`);
}
engine = engine.replace(
  /const VERSION = \"20260818-browser-direct-v[^\"]+\";/,
  'const VERSION = "20260819-browser-direct-v8-global-recovery";',
);
fs.writeFileSync(enginePath, engine, "utf8");

let run = read(runPath);
run = replaceOnce(
  run,
  "  }, 4000);",
  "  }, 10000);",
  "direct status polling throttle",
);
fs.writeFileSync(runPath, run, "utf8");

console.log("Global recovery v1 finalized: fixed equal Split stake, 0.50 minimum, lower status-poll load");
