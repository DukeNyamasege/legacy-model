import fs from "node:fs";

const path = "dist/direct-run-panel-authority-v6.js";
if (!fs.existsSync(path)) {
  throw new Error(`prepare-terminal-runpanel-v1 missing build artifact: ${path}`);
}

let source = fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
const startMarker = `  async function readServerStatus() {`;
const endMarker = `  function xhrStop() {`;
const start = source.indexOf(startMarker);
const end = start >= 0 ? source.indexOf(endMarker, start + startMarker.length) : -1;
if (start < 0 || end < 0) {
  throw new Error("prepare-terminal-runpanel-v1 could not resolve readServerStatus boundaries");
}

const replacement = `  async function readServerStatus() {\n    if (browserRunning()) {\n      state.serverActive = false;\n      state.serverOwner = "browser";\n      queueRender();\n      return;\n    }\n    try {\n      const response = await window.fetch(STATUS_URL, {\n        credentials: "include",\n        cache: "no-store",\n        headers: { Accept: "application/json" },\n      });\n      if (!response.ok) return;\n      const payload = await response.json();\n      const runtime = payload?.runtime_state && typeof payload.runtime_state === "object"\n        ? payload.runtime_state\n        : payload;\n      const live = {\n        owner: String(runtime?.owner || payload?.owner || "stopped").toLowerCase(),\n        status: String(runtime?.status || payload?.status || "").toLowerCase(),\n        enabled: runtime?.enabled ?? payload?.enabled,\n        running: runtime?.running ?? payload?.running,\n      };\n      const terminalStatus = [\n        "hard_stopped",\n        "stopped_manual",\n        "manual_stop",\n        "stopped_take_profit",\n        "take_profit",\n        "stopped_stop_loss",\n        "stop_loss",\n      ].some((token) => live.status === token || live.status.includes(token));\n      const remoteStopped = Boolean(payload?.hard_stop === true || terminalStatus);\n      const serverSaysActive = Boolean(\n        live.enabled === true\n        || live.running === true\n        || (live.owner && live.owner !== "stopped")\n      );\n      state.serverOwner = remoteStopped ? "stopped" : live.owner;\n      state.serverActive = !remoteStopped && serverSaysActive;\n      if (remoteStopped) state.userStopLatch = true;\n      queueRender();\n    } catch (_) {\n      // Status is advisory. Recoverable transport/status failures must never\n      // disable Auto Trading or manufacture a terminal Stop.\n    }\n  }\n\n`;

source = source.slice(0, start) + replacement + source.slice(end);

const canonicalBlock = `    const terminalStatus = [\n      "hard_stopped",\n      "stopped_manual",\n      "manual_stop",\n      "stopped_take_profit",\n      "take_profit",\n      "stopped_stop_loss",\n      "stop_loss",\n    ].some((token) => live.status === token || live.status.includes(token));\n    const remoteStopped = Boolean(payload?.hard_stop === true || terminalStatus);`;

if (!source.includes(canonicalBlock)) {
  throw new Error("prepare-terminal-runpanel-v1 canonical terminal-only block missing");
}
if (source.includes("payload?.enabled === false")) {
  throw new Error("prepare-terminal-runpanel-v1 stale enabled=false stop authority remains");
}
if (source.includes('live.status === "stopped"') || source.includes('live.owner === "stopped"')) {
  throw new Error("prepare-terminal-runpanel-v1 generic stopped state can still become terminal");
}

fs.writeFileSync(path, source, "utf8");
console.log("PREPARE_TERMINAL_RUNPANEL_V1_INSTALLED hard_stop_tp_sl_manual_only=true recoverable_status_advisory=true");
