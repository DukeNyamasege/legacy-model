import fs from "node:fs";

const path = "dist/direct-run-panel-authority-v6.js";
if (!fs.existsSync(path)) {
  throw new Error(`prepare-terminal-runpanel-v1 missing build artifact: ${path}`);
}

let source = fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");

function replaceBetween(startMarker, endMarker, replacement, label) {
  const start = source.indexOf(startMarker);
  const end = start >= 0 ? source.indexOf(endMarker, start + startMarker.length) : -1;
  if (start < 0 || end < 0) {
    throw new Error(`prepare-terminal-runpanel-v1 could not resolve ${label} boundaries`);
  }
  source = source.slice(0, start) + replacement + source.slice(end);
}

// Keep this block byte-identical to the installed shape accepted by
// finalize-exact-builder-live-diagnostics-v2. Indentation is deliberately four
// spaces here even though the statements execute inside the surrounding try.
const canonicalBlock = `    const terminalStatus = [\n      "hard_stopped",\n      "stopped_manual",\n      "manual_stop",\n      "stopped_take_profit",\n      "take_profit",\n      "stopped_stop_loss",\n      "stop_loss",\n    ].some((token) => live.status === token || live.status.includes(token));\n    const remoteStopped = Boolean(payload?.hard_stop === true || terminalStatus);`;

const statusReplacement = `  async function readServerStatus() {\n    if (browserRunning()) {\n      state.serverActive = false;\n      state.serverOwner = "browser";\n      queueRender();\n      return;\n    }\n    try {\n      const response = await window.fetch(STATUS_URL, {\n        credentials: "include",\n        cache: "no-store",\n        headers: { Accept: "application/json" },\n      });\n      if (!response.ok) return;\n      const payload = await response.json();\n      const runtime = payload?.runtime_state && typeof payload.runtime_state === "object"\n        ? payload.runtime_state\n        : payload;\n      const live = {\n        owner: String(runtime?.owner || payload?.owner || "stopped").toLowerCase(),\n        status: String(runtime?.status || payload?.status || "").toLowerCase(),\n        enabled: runtime?.enabled ?? payload?.enabled,\n        running: runtime?.running ?? payload?.running,\n      };\n${canonicalBlock}\n      const serverSaysActive = Boolean(\n        live.enabled === true\n        || live.running === true\n        || (live.owner && live.owner !== "stopped")\n      );\n      state.serverOwner = remoteStopped ? "stopped" : live.owner;\n      state.serverActive = !remoteStopped && serverSaysActive;\n      if (remoteStopped) state.userStopLatch = true;\n      queueRender();\n    } catch (_) {\n      // Status is advisory. Recoverable transport/status failures must never\n      // disable Auto Trading or manufacture a terminal Stop.\n    }\n  }\n\n`;
replaceBetween(
  `  async function readServerStatus() {`,
  `  function xhrStop() {`,
  statusReplacement,
  "readServerStatus",
);

// Runtime-safety-v2 installs a separate cross-device applyRemoteStop helper.
// Normalize it before the exact diagnostics finalizer so enabled=false, generic
// stopped ownership, network recovery, and temporary runtime state cannot stop
// a healthy browser-direct execution session.
if (source.includes(`  function applyRemoteStop(payload) {`)) {
  const remoteStopReplacement = `  function applyRemoteStop(payload) {\n    const status = String(payload?.execution_status || payload?.status || "").toLowerCase();\n    const explicitTerminal = [\n      "stopped_manual",\n      "manual_stop",\n      "stopped_take_profit",\n      "take_profit",\n      "stopped_stop_loss",\n      "stop_loss",\n    ].some((token) => status === token || status.includes(token));\n    const stopped = Boolean(payload?.hard_stop === true || explicitTerminal);\n    if (!stopped || !browserRunning()) return;\n    try { window.DERIVADMIN_DIRECT_HARD_STOP_FENCE_V1?.hard_stop?.(); } catch (_) {}\n    try { engine()?.stop?.(String(payload?.execution_status_reason || "Stopped on another logged-in device")); } catch (_) {}\n    state.userStopLatch = true;\n    state.serverActive = false;\n    state.serverOwner = "stopped";\n  }\n\n`;
  replaceBetween(
    `  function applyRemoteStop(payload) {`,
    `  function ensureJournalDiagnostic() {`,
    remoteStopReplacement,
    "applyRemoteStop",
  );
}

if (!source.includes(canonicalBlock)) {
  throw new Error("prepare-terminal-runpanel-v1 canonical terminal-only block missing");
}
for (const forbidden of [
  "payload?.enabled === false",
  'live.status === "stopped"',
  'live.owner === "stopped"',
]) {
  if (source.includes(forbidden)) {
    throw new Error(`prepare-terminal-runpanel-v1 stale generic stop authority remains: ${forbidden}`);
  }
}
for (const required of [
  "payload?.hard_stop === true || terminalStatus",
  "payload?.hard_stop === true || explicitTerminal",
  '"stopped_manual"',
  '"manual_stop"',
  '"stopped_take_profit"',
  '"take_profit"',
  '"stopped_stop_loss"',
  '"stop_loss"',
]) {
  if (!source.includes(required)) {
    throw new Error(`prepare-terminal-runpanel-v1 required terminal authority missing: ${required}`);
  }
}

fs.writeFileSync(path, source, "utf8");
console.log("PREPARE_TERMINAL_RUNPANEL_V1_INSTALLED hard_stop_tp_sl_manual_only=true recoverable_status_advisory=true canonical_shape=true cross_device=true");
