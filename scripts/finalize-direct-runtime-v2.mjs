import fs from "node:fs";
import path from "node:path";

const file = path.join(process.cwd(), "dist", "deriv-direct-execution-v2.js");
let source = fs.readFileSync(file, "utf8");

const before = `  function onTick(symbol, tick) {\n    const history = recordTick(symbol, tick);\n    if (!history || !state.running || !state.strategy || state.ownerLost) return;`;
const after = `  function onTick(symbol, tick) {\n    const history = recordTick(symbol, tick);\n    if (!history || !state.running || !state.strategy || state.ownerLost) return;\n    // History hydration primes 100/500/1000-tick conditions only. A provider BUY\n    // may be initiated exclusively by a subsequent real-time tick.\n    if (tick?.__history_hydration) return;`;

const count = source.split(before).length - 1;
if (count !== 1) {
  throw new Error(`Direct runtime hydration guard expected one onTick entry point, found ${count}`);
}
source = source.replace(before, after);
fs.writeFileSync(file, source, "utf8");
console.log("Direct runtime history hydration is observation-only; live ticks own entry execution");
