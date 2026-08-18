import fs from "node:fs";
import path from "node:path";

const file = path.join(process.cwd(), "dist", "deriv-direct-execution-v2.js");
let source = fs.readFileSync(file, "utf8");

function replaceOnce(label, before, after) {
  const first = source.indexOf(before);
  if (first < 0) throw new Error(`Production v6b patch missing: ${label}`);
  if (source.indexOf(before, first + before.length) >= 0) {
    throw new Error(`Production v6b patch ambiguous: ${label}`);
  }
  source = source.slice(0, first) + after + source.slice(first + before.length);
}

replaceOnce(
  "settled direct journal includes exact entry spot",
  `      session_profit: state.sessionProfit,\n      exit_spot: contract?.exit_spot ?? contract?.current_spot ?? null,`,
  `      session_profit: state.sessionProfit,\n      entry_spot: contract?.entry_spot ?? contract?.entry_tick ?? null,\n      exit_spot: contract?.exit_spot ?? contract?.current_spot ?? null,`,
);

replaceOnce(
  "export persistent split takeover state",
  `      recovery_debt: state.recoveryDebt,\n      consecutive_losses: state.consecutiveLosses,`,
  `      recovery_debt: state.recoveryDebt,\n      split_basis_debt: state.splitBasisDebt,\n      split_remaining_wins: state.splitRemainingWins,\n      consecutive_losses: state.consecutiveLosses,`,
);

fs.writeFileSync(file, source, "utf8");
console.log("Production v6b finalized: direct transaction entry spot and exact Split takeover state exported");
