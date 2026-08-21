import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`start-arm-reconciliation missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) {
    throw new Error(`start-arm-reconciliation ${label}: expected 1 source match or installed shape, got ${count}`);
  }
  return source.replace(before, after);
}

function replaceBetween(source, start, end, replacement, label) {
  const a = source.indexOf(start);
  const b = a >= 0 ? source.indexOf(end, a + start.length) : -1;
  if (a < 0 || b < 0) throw new Error(`start-arm-reconciliation ${label}: source boundaries missing`);
  return source.slice(0, a) + replacement + source.slice(b);
}

// A browser timeout does not prove that the VPS transaction failed. The server can
// commit /arm after the AbortController has already rejected the browser fetch. If
// we simply POST /arm again, the browser stays unarmed locally and repeats the same
// fresh-Start persistence work. Reconcile the already-persisted browser epoch first.
let engine = read(enginePath);
const ownership = `  function waitForArmReconcile(delayMs) {\n    return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(delayMs || 0))));\n  }\n\n  function acceptReconciledArm(epoch, payload) {\n    if (!state.running || state.epoch !== epoch || state.ownerLost) return false;\n    const sameEpoch = String(payload?.epoch || \"\") === String(epoch || \"\");\n    const owner = String(payload?.owner || \"\").toLowerCase();\n    const status = String(payload?.execution_status || \"\").toLowerCase();\n    const browserOwned = owner === \"browser\" && (status === \"direct_browser\" || status === \"browser_direct\" || status === \"browser_direct_only\");\n    const financiallyAllowed = payload?.purchase_allowed !== false\n      && payload?.hard_stop !== true\n      && payload?.enabled !== false;\n    if (!sameEpoch || !browserOwned || !financiallyAllowed) return false;\n    state.armed = true;\n    state.ownerLost = false;\n    state.lastLeaseAckAt = Date.now();\n    state.leaseMs = Number.MAX_SAFE_INTEGER;\n    state.lastExecutionError = \"\";\n    return true;\n  }\n\n  async function reconcileArm(epoch, attempts = 3) {\n    const total = Math.max(1, Math.min(5, Number(attempts || 1)));\n    for (let attempt = 0; attempt < total; attempt += 1) {\n      if (!state.running || state.epoch !== epoch || state.ownerLost || state.armed) return Boolean(state.armed);\n      try {\n        const response = await fetchWithTimeout(\n          apiPath(\"/me/runtime-sync\"),\n          {\n            method: \"GET\",\n            credentials: \"include\",\n            cache: \"no-store\",\n            headers: { Accept: \"application/json\" },\n          },\n          6000,\n        );\n        if (response.ok) {\n          const payload = await response.json();\n          if (acceptReconciledArm(epoch, payload)) return true;\n        }\n      } catch (_) {}\n      if (attempt + 1 < total) await waitForArmReconcile(750);\n    }\n    return false;\n  }\n\n  async function armOnce(epoch, strategy) {\n    // First reconcile the same Start epoch. This makes retries idempotent from the\n    // browser even when the preceding POST completed after its client timeout.\n    if (await reconcileArm(epoch, 1)) return true;\n\n    let response;\n    try {\n      response = await fetchWithTimeout(\n        apiPath(\"/me/direct-execution/arm\"),\n        {\n          method: \"POST\",\n          credentials: \"include\",\n          headers: { \"Content-Type\": \"application/json\" },\n          body: JSON.stringify({ epoch, strategy }),\n        },\n        20000,\n      );\n    } catch (error) {\n      // Abort/network errors can race a successful server commit. Give the VPS a\n      // short bounded reconciliation window before issuing another arm write.\n      if (await reconcileArm(epoch, 4)) return true;\n      throw error;\n    }\n\n    if (!response.ok) {\n      let detail = \"\";\n      try {\n        const failure = await response.clone().json();\n        detail = String(failure?.detail || failure?.message || \"\").slice(0, 180);\n      } catch (_) {}\n      // A reverse proxy can report a failure after the application transaction has\n      // committed. Same-epoch server state is stronger evidence than that response.\n      if (await reconcileArm(epoch, 2)) return true;\n      throw new Error(detail || (\"Start control unavailable HTTP \" + response.status));\n    }\n\n    const payload = await response.json();\n    if (!state.running || state.epoch !== epoch || state.ownerLost) return false;\n    if (String(payload?.epoch || \"\") !== String(epoch || \"\")) {\n      throw new Error(\"Start control returned a different browser execution epoch\");\n    }\n    state.armed = true;\n    state.ownerLost = false;\n    state.lastLeaseAckAt = Date.now();\n    state.leaseMs = Number.MAX_SAFE_INTEGER;\n    state.lastExecutionError = \"\";\n    return true;\n  }\n\n  function armInBackground(epoch, strategy) {\n    let retryMs = 3000;\n    const attempt = async () => {\n      if (!state.running || state.epoch !== epoch || state.ownerLost || state.armed) return;\n      try {\n        if (await armOnce(epoch, strategy)) {\n          updateStatus(\"Direct • browser owns execution • connecting straight to Deriv\");\n          connectPrivate().catch(() => {});\n          subscribeMarkets();\n          return;\n        }\n      } catch (error) {\n        state.lastExecutionError = String(error?.message || error || \"Start control unavailable\")\n          .replace(/bearer\\s+[^\\s]+/gi, \"Bearer [redacted]\")\n          .replace(/otp=[^&\\s]+/gi, \"otp=[redacted]\")\n          .slice(0, 180);\n      }\n      if (state.running && state.epoch === epoch && !state.ownerLost && !state.armed) {\n        const delay = retryMs;\n        retryMs = Math.min(15000, Math.max(3000, Math.round(retryMs * 1.7)));\n        setTimeout(attempt, delay);\n      }\n    };\n    attempt();\n  }\n\n`;

engine = replaceBetween(
  engine,
  "  async function armOnce(epoch, strategy) {",
  "  async function heartbeatOnce(_epoch) {",
  ownership,
  "same-epoch Start arm reconciliation",
);

// The 60-second diagnosis is generated into the execution engine by the exact
// Builder diagnostics finalizer. Patch that finalized engine directly so the
// current Start failure is visible instead of being hidden by a generic message.
engine = replaceOne(
  engine,
  '    if (!state.armed) return "Auto Trading is ON; Start control synchronization is retrying. Browser execution is not armed yet.";',
  `    if (!state.armed) {\n      const startError = String(diagnostics.last_execution_error || \"\")\n        .replace(/bearer\\s+[^\\s]+/gi, \"Bearer [redacted]\")\n        .replace(/otp=[^&\\s]+/gi, \"otp=[redacted]\")\n        .slice(0, 160);\n      return startError\n        ? \`Auto Trading is ON; Start control synchronization is retrying. Browser execution is not armed yet. Last Start error: \${startError}\`\n        : \"Auto Trading is ON; Start control synchronization is retrying. Browser execution is not armed yet.\";\n    }`,
  "visible Start failure diagnosis",
);

for (const required of [
  'apiPath("/me/runtime-sync")',
  "acceptReconciledArm",
  "reconcileArm(epoch, 4)",
  "20000",
  "Start control returned a different browser execution epoch",
  "connectPrivate().catch(() => {})",
  "Last Start error:",
]) {
  if (!engine.includes(required)) throw new Error(`start-arm-reconciliation engine invariant missing: ${required}`);
}
if (engine.includes('apiPath("/me/direct-execution/heartbeat")')) {
  throw new Error("start-arm-reconciliation must not reintroduce periodic financial heartbeat traffic");
}
write(enginePath, engine);

// Cache-bust the browser execution engine. Both the reconciliation logic and its
// 60-second diagnostic live in this same asset.
let index = read(indexPath);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260821-start-arm-reconcile-v2",
);
if (!index.includes("/deriv-direct-execution-v2.js?v=20260821-start-arm-reconcile-v2")) {
  throw new Error("start-arm-reconciliation engine cache invariant missing");
}
write(indexPath, index);

console.log("START_ARM_RECONCILIATION_V1_INSTALLED same_epoch_reconcile=true arm_timeout_ms=20000 periodic_heartbeat=false visible_start_error=true");
