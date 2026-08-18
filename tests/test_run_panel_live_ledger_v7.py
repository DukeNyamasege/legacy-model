from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RunPanelLiveLedgerV7Contract(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_direct_ledger_owns_rows_and_kpis_from_one_contract_snapshot(self) -> None:
        ledger = self.text("dashboard/direct-transaction-ledger-v6.js")
        self.assertIn("DIRECT TRANSACTION + KPI AUTHORITY", ledger)
        self.assertIn("version: \"20260818-direct-transaction-ledger-v7\"", ledger)
        self.assertIn("function stats(rows)", ledger)
        self.assertIn("totalStake += stake", ledger)
        self.assertIn("runs: rows.length", ledger)
        self.assertIn("summary.innerHTML = statsMarkup(stats(rows))", ledger)
        self.assertIn("body.innerHTML =", ledger)
        self.assertIn("direct-canonical-table-v7", ledger)

    def test_direct_ledger_has_no_periodic_reinsertion_timer(self) -> None:
        ledger = self.text("dashboard/direct-transaction-ledger-v6.js")
        self.assertNotIn("setInterval(", ledger)
        self.assertIn("MutationObserver", ledger)
        self.assertIn("observer?.disconnect", ledger)
        self.assertIn("if (!applying", ledger)

    def test_reset_is_locked_while_execution_is_running(self) -> None:
        usability = self.text("dashboard/run-panel-usability-v1.js")
        self.assertIn('dataset.finalRunState === "running"', usability)
        self.assertIn("button.disabled = isRunning", usability)
        self.assertIn("Stop the bot before resetting trades", usability)
        self.assertIn('pointer-events:none', usability)

    def test_run_panel_typography_is_larger(self) -> None:
        usability = self.text("dashboard/run-panel-usability-v1.js")
        self.assertIn("font-size:15px", usability)
        self.assertIn("font-size:11.5px", usability)
        self.assertIn("font-size:13px", usability)
        self.assertIn("font-size:20px", usability)

    def test_frontend_ships_cache_busted_v7_ledger_and_usability_last(self) -> None:
        docker = self.text("Dockerfile.frontend")
        self.assertIn("direct-ledger-v7", docker)
        self.assertIn("run-panel-usability-v1.js?v=20260818-run-panel-usability-v1", docker)
        self.assertIn("node --check dist/run-panel-usability-v1.js", docker)
        self.assertGreater(
            docker.index("run-panel-usability-v1.js?v=20260818-run-panel-usability-v1"),
            docker.index("mobile-layout-authority-v1.js?v=20260818-mobile-layout-v1"),
        )


if __name__ == "__main__":
    unittest.main()
