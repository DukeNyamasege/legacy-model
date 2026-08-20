import fs from "node:fs";

const path = "dist/direct-run-panel-authority-v6.js";
if (!fs.existsSync(path)) throw new Error(`runtime-safety preparation missing ${path}`);
let source = fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");

const before = `      state.userStopLatch = false;\n      setTimeout(queueRender, 0);\n      setTimeout(queueRender, 80);`;
const after = `      state.userStopLatch = false;\n      setTimeout(() => queueRender(), 0);\n      setTimeout(queueRender, 80);`;
const count = source.split(before).length - 1;
if (count === 1) {
  source = source.replace(before, after);
} else if (count === 0 && source.includes(after)) {
  // Idempotent candidate rebuild.
} else {
  throw new Error(`runtime-safety preparation start-render target expected 1 match, got ${count}`);
}

if ((source.split("setTimeout(queueRender, 0);").length - 1) !== 1) {
  throw new Error("runtime-safety preparation did not leave exactly one Journal-tab render target");
}

fs.writeFileSync(path, source, "utf8");
console.log("Runtime safety preparation complete: final Run-panel target is unambiguous");
