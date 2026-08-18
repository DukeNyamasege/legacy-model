from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RunPanelLedgerV9Contract(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_browser_and_server_rows_share_one_retained_snapshot_without_timer(self) -> None:
        ledger = self.text("dashboard/direct-transaction-ledger-v6.js")
        self.assertIn("__DERIVADMIN_DIRECT_TRANSACTION_LEDGER_V9__", ledger)
        self.assertIn("derivadmin-unified-ledger-snapshot-v9:", ledger)
        self.assertIn("function serverContracts()", ledger)
        self.assertIn("function directContracts()", ledger)
        self.assertIn("window.FOA_FINAL_UI?.state?.()", ledger)
        self.assertIn("for (const row of retainedRows(key))", ledger)
        self.assertIn("for (const row of serverContracts())", ledger)
        self.assertIn("for (const row of directContracts())", ledger)
        self.assertIn("summary.innerHTML = statsMarkup(values)", ledger)
        self.assertIn("observer = new MutationObserver", ledger)
        self.assertNotIn("setInterval", ledger)
        self.assertNotIn("requestAnimationFrame(() => render", ledger)

    def test_market_column_renders_friendly_name_only(self) -> None:
        ledger = self.text("dashboard/direct-transaction-ledger-v6.js")
        self.assertIn('"1HZ100V": "V100 1S"', ledger)
        self.assertIn('"R_100": "V100"', ledger)
        self.assertNotIn("`${labels[raw]} · ${raw}`", ledger)
        self.assertIn("${esc(marketLabel(row.symbol))}", ledger)

    def test_mobile_nav_is_above_run_panel_and_balance_is_centered(self) -> None:
        ux = self.text("dashboard/run-panel-usability-v1.js")
        self.assertIn('dataset.runPanelVisibility = panelState', ux)
        self.assertIn('html[data-run-panel-visibility="open"] .bottom-nav', ux)
        self.assertIn('html[data-run-panel-visibility="collapsed"] .bottom-nav', ux)
        self.assertIn("z-index:420!important", ux)
        self.assertIn("margin-bottom:64px!important", ux)
        self.assertIn(".top-account-switch .account-switch-summary", ux)
        self.assertIn("grid-template-columns:auto auto minmax(0,1fr) auto", ux)
        self.assertIn("text-align:center!important", ux)
        self.assertIn("text-overflow:clip!important", ux)

    def test_frontend_cache_busts_unified_v9_and_scheduler_status(self) -> None:
        dockerfile = self.text("Dockerfile.frontend")
        self.assertIn("/direct-transaction-ledger-v6.js?v=20260818-unified-ledger-v9", dockerfile)
        self.assertIn("/direct-run-panel-authority-v6.js?v=20260818-scheduler-start-stop-v2", dockerfile)
        self.assertIn("/run-panel-usability-v1.js?v=20260818-run-panel-usability-v2", dockerfile)
        self.assertIn("node --check dist/direct-transaction-ledger-v6.js", dockerfile)
        self.assertIn("node --check dist/direct-run-panel-authority-v6.js", dockerfile)


if __name__ == "__main__":
    unittest.main()
