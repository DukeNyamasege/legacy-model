import fs from "node:fs";

const path = "dist/direct-run-panel-authority-v6.js";
let source = fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");

// `hard_stopped` by itself is too generic for browser-direct execution. The
// durable hard_stop flag remains authoritative, while explicit manual/TP/SL
// statuses are accepted as terminal mirrors. Everything else is recoverable.
source = source.replace(
  `    const terminalStatus = [\n      "hard_stopped",\n      "stopped_manual",`,
  `    const terminalStatus = [\n      "stopped_manual",`,
);

for (const forbidden of [
  "payload?.enabled === false",
  'live.status === "stopped"',
  'live.owner === "stopped"',
  '      "hard_stopped",',
]) {
  if (source.includes(forbidden)) throw new Error(`non-authoritative terminal stop survived: ${forbidden}`);
}
for (const required of [
  "payload?.hard_stop === true || terminalStatus",
  '"stopped_manual"',
  '"manual_stop"',
  '"stopped_take_profit"',
  '"take_profit"',
  '"stopped_stop_loss"',
  '"stop_loss"',
]) {
  if (!source.includes(required)) throw new Error(`terminal stop authority missing: ${required}`);
}

fs.writeFileSync(path, source, "utf8");
console.log("TERMINAL_STOP_ONLY_V1_INSTALLED tp=true sl=true manual=true generic_server_state=false");
