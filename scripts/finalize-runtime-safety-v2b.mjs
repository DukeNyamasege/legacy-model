import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
if (!fs.existsSync(enginePath)) throw new Error(`runtime-safety-v2b missing ${enginePath}`);
let engine = fs.readFileSync(enginePath, "utf8").replace(/\r\n/g, "\n");

const before = `    const anchor = Number(state.lastRealPurchaseAt || state.runStartedAt || Date.now());\n    if (Date.now() - anchor < 60000) return;`;
const after = `    const browserAnchor = Number(state.lastRealPurchaseAt || state.runStartedAt || Date.now());\n    const serverPurchaseAt = Date.parse(String(runtimeSyncSnapshot().last_purchase_at || ""));\n    const anchor = Number.isFinite(serverPurchaseAt) ? Math.max(browserAnchor, serverPurchaseAt) : browserAnchor;\n    if (Date.now() - anchor < 60000) return;`;
const count = engine.split(before).length - 1;
if (count === 1) {
  engine = engine.replace(before, after);
} else if (count === 0 && engine.includes(after)) {
  // Idempotent candidate rebuild.
} else {
  throw new Error(`runtime-safety-v2b purchase clock expected 1 source match or installed shape, got ${count}`);
}

for (const required of [
  "serverPurchaseAt",
  "runtimeSyncSnapshot().last_purchase_at",
  "Math.max(browserAnchor, serverPurchaseAt)",
  "NO TRADE PURCHASED AFTER 60 SECONDS",
]) {
  if (!engine.includes(required)) throw new Error(`runtime-safety-v2b invariant missing: ${required}`);
}

fs.writeFileSync(enginePath, engine, "utf8");
console.log("Runtime safety v2b finalized: browser and VPS purchases share the 60-second diagnostic clock");
