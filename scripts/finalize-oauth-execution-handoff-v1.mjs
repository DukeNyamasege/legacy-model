import fs from "node:fs";

const enginePath = "dist/deriv-direct-execution-v2.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`oauth-execution-handoff missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}
function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}
function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`oauth-execution-handoff ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

let engine = read(enginePath);

const yieldHelper = `  async function yieldUnhealthyBrowserExecution(reason) {\n    if (!state.running || state.ownerLost) return false;\n    const epoch = state.epoch;\n    const message = String(reason || "Authenticated Deriv trade channel unavailable").slice(0, 160);\n\n    // Financial ownership is surrendered locally before any network request. From\n    // this line onward the browser cannot send another proposal/BUY. The server\n    // call only accelerates the already-safe lease-expiry takeover path.\n    state.ownerLost = true;\n    state.armed = false;\n    state.privateUnavailableSince = 0;\n    state.lastBlockingReason = "Browser trade channel unavailable; VPS continuity takeover activated automatically.";\n    clearInterval(state.heartbeatTimer);\n    state.heartbeatTimer = null;\n    updateStatus("Server continuity • authenticated browser trade channel unavailable • VPS takeover activated");\n    window.dispatchEvent(new CustomEvent("derivadmin:direct-execution-yield", {\n      detail: { reason: message, epoch, auto_trading_continues: true, terminal_stop: false },\n    }));\n\n    try {\n      const response = await fetchWithTimeout(\n        apiPath("/me/direct-execution/yield"),\n        {\n          method: "POST",\n          headers: { "Content-Type": "application/json" },\n          body: JSON.stringify({ epoch, reason: message }),\n          keepalive: true,\n        },\n        5500,\n      );\n      if (!response.ok && response.status === 409) {\n        state.lastBlockingReason = "Execution ownership already moved; VPS continuity remains authoritative.";\n      }\n    } catch (_) {\n      // Do not resume heartbeats on failure. The ordinary 20-second lease expiry is\n      // the fail-safe and the worker will still take over without a terminal Stop.\n    }\n    return true;\n  }\n\n`;

engine = replaceOne(
  engine,
  `  function ownershipWatch() {`,
  yieldHelper + `  function ownershipWatch() {`,
  "automatic browser-to-VPS yield helper",
);

const oldOwnershipWatch = `  function ownershipWatch() {\n    clearInterval(state.heartbeatTimer);\n    state.heartbeatTimer = setInterval(() => {\n      if (!state.running || state.ownerLost) return;\n      if (state.lastLeaseAckAt && Date.now() - state.lastLeaseAckAt > state.leaseMs - LEASE_SAFETY_MS) {\n        state.ownerLost = true;\n        updateStatus("Server continuity • browser surrendered execution ownership");\n        return;\n      }\n      heartbeatOnce(state.epoch).catch(() => {});\n    }, HEARTBEAT_MS);\n  }`;

const newOwnershipWatch = `  function ownershipWatch() {\n    clearInterval(state.heartbeatTimer);\n    state.privateUnavailableSince = 0;\n    state.heartbeatTimer = setInterval(() => {\n      if (!state.running || state.ownerLost) return;\n      if (state.lastLeaseAckAt && Date.now() - state.lastLeaseAckAt > state.leaseMs - LEASE_SAFETY_MS) {\n        yieldUnhealthyBrowserExecution("Browser ownership heartbeat could not be confirmed in time").catch(() => {});\n        return;\n      }\n\n      // A browser heartbeat is a FINANCIAL ownership assertion. Never renew it\n      // while the authenticated private Deriv socket is unavailable: doing so\n      // fences the healthy VPS worker while the browser itself cannot BUY.\n      if (state.privateWs?.readyState !== WebSocket.OPEN) {\n        if (!state.privateUnavailableSince) state.privateUnavailableSince = Date.now();\n        if (Date.now() - state.privateUnavailableSince >= 5000) {\n          yieldUnhealthyBrowserExecution(\n            state.lastExecutionError || "Authenticated Deriv trading session is not connected",\n          ).catch(() => {});\n        }\n        return;\n      }\n\n      state.privateUnavailableSince = 0;\n      heartbeatOnce(state.epoch).catch(() => {});\n    }, HEARTBEAT_MS);\n  }`;
engine = replaceOne(engine, oldOwnershipWatch, newOwnershipWatch, "financially healthy heartbeat ownership");

engine = replaceOne(
  engine,
  `    state.lastLeaseAckAt = 0;\n    state.histories.clear();`,
  `    state.lastLeaseAckAt = 0;\n    state.privateUnavailableSince = 0;\n    state.histories.clear();`,
  "start resets private transport grace",
);

// Runtime safety's no-purchase diagnostic should describe the handoff, not repeat
// a stale server reason saying the browser owns execution while ownerLost is true.
engine = replaceOne(
  engine,
  `    if (state.ownerLost) {\n      return serverReason || "Browser execution lease moved to VPS continuity; server recovery is taking over.";\n    }`,
  `    if (state.ownerLost) {\n      return state.lastBlockingReason || serverReason || "Browser execution lease moved to VPS continuity; server recovery is taking over.";\n    }`,
  "yield diagnosis prefers live handoff reason",
);

engine = replaceOne(
  engine,
  `        no_purchase_since_ms: state.running ? Math.max(0, Date.now() - Number(state.lastRealPurchaseAt || state.runStartedAt || Date.now())) : 0,`,
  `        no_purchase_since_ms: state.running ? Math.max(0, Date.now() - Number(state.lastRealPurchaseAt || state.runStartedAt || Date.now())) : 0,\n        private_unavailable_ms: state.privateUnavailableSince ? Math.max(0, Date.now() - Number(state.privateUnavailableSince)) : 0,\n        browser_financial_owner_healthy: Boolean(!state.ownerLost && state.armed && state.privateWs?.readyState === WebSocket.OPEN),`,
  "handoff diagnostics state export",
);

for (const required of [
  "yieldUnhealthyBrowserExecution",
  "/me/direct-execution/yield",
  "browser_financial_owner_healthy",
  "private_unavailable_ms",
  "Never renew it",
  "VPS continuity takeover activated automatically",
]) {
  if (!engine.includes(required)) throw new Error(`oauth-execution-handoff engine invariant missing: ${required}`);
}
write(enginePath, engine);

let index = read(indexPath);
index = index.replace(
  /\/deriv-direct-execution-v2\.js\?v=[^"']+/g,
  "/deriv-direct-execution-v2.js?v=20260820-oauth-auto-handoff-v13",
);
if (!index.includes("/deriv-direct-execution-v2.js?v=20260820-oauth-auto-handoff-v13")) {
  throw new Error("oauth-execution-handoff engine cache-bust missing");
}
write(indexPath, index);

console.log("OAuth execution handoff v1 finalized: one Deriv login, automatic private OTP bootstrap, unhealthy browser lease yields to VPS without stopping Auto Trading");
