import fs from "node:fs";
import path from "node:path";

const file = path.join(process.cwd(), "dist", "deriv-direct-execution-v2.js");
let source = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");

function ensureEntrySpot() {
  if (source.includes("entry_spot:") && source.includes("actual_last_digit:")) return;
  if (source.includes("entry_spot: contract?.entry_spot ?? contract?.entry_tick ?? null")) return;
  const before = `      session_profit: state.sessionProfit,\n      exit_spot: contract?.exit_spot ?? contract?.current_spot ?? null,`;
  const after = `      session_profit: state.sessionProfit,\n      entry_spot: contract?.entry_spot ?? contract?.entry_tick ?? null,\n      exit_spot: contract?.exit_spot ?? contract?.current_spot ?? null,`;
  const count = source.split(before).length - 1;
  if (count !== 1) throw new Error(`Production v6b entry-spot anchor expected one match, got ${count}`);
  source = source.replace(before, after);
}

function ensureSplitTakeoverExport() {
  const required = [
    "split_basis_debt: state.splitBasisDebt",
    "split_remaining_wins: state.splitRemainingWins",
    "split_part_stake: state.splitPartStake",
  ];
  if (required.every((marker) => source.includes(marker))) return;
  if (required.some((marker) => source.includes(marker))) {
    throw new Error("Production v6b Split takeover export is only partially installed");
  }

  const needle = "recovery_debt: state.recoveryDebt,";
  const count = source.split(needle).length - 1;
  if (count !== 1) throw new Error(`Production v6b recovery-debt export expected one match, got ${count}`);

  const start = source.indexOf(needle);
  const lineStart = source.lastIndexOf("\n", start) + 1;
  const indent = source.slice(lineStart, start);
  const insertion = [
    needle,
    `${indent}split_basis_debt: state.splitBasisDebt,`,
    `${indent}split_remaining_wins: state.splitRemainingWins,`,
    `${indent}split_part_stake: state.splitPartStake,`,
  ].join("\n");
  source = source.slice(0, start) + insertion + source.slice(start + needle.length);
}

ensureEntrySpot();
ensureSplitTakeoverExport();

for (const required of [
  "entry_spot:",
  "split_basis_debt: state.splitBasisDebt",
  "split_remaining_wins: state.splitRemainingWins",
  "split_part_stake: state.splitPartStake",
]) {
  if (!source.includes(required)) throw new Error(`Production v6b invariant missing after finalize: ${required}`);
}

fs.writeFileSync(file, source, "utf8");
console.log("Production v6b finalized: direct entry spot and fixed equal Split takeover state exported");
