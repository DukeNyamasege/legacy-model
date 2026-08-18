from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RunPanelLedgerV8Contract(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_direct_rows_and_kpis_share_retained_snapshot_without_timer(self) -> None:
        ledger = self.text("dashboard/direct-transaction-ledger-v6.js")
        self.assertIn("DIRECT TRANSACTION + KPI AUTHORITY V8", ledger)
        self.assertIn("derivadmin-direct-ledger-snapshot-v8:", ledger)
        self.assertIn("rememberRows(key, live)", ledger)
        self.assertIn("return retainedRows(key)", ledger)
        self.assertIn("summary.innerHTML = statsMarkup(values)", ledger)
        self.assertIn("observer = new MutationObserver", ledger)
        self.assertIn("render(true)", ledger)
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

    def test_frontend_cache_busts_v8_and_usability_v2(self) -> None:
        dockerfile = self.text("Dockerfile.frontend")
        self.assertIn("/direct-transaction-ledger-v6.js?v=20260818-direct-ledger-v8", dockerfile)
        self.assertIn("/run-panel-usability-v1.js?v=20260818-run-panel-usability-v2", dockerfile)
        self.assertIn("node --check dist/direct-transaction-ledger-v6.js", dockerfile)
        self.assertIn("node --check dist/run-panel-usability-v1.js", dockerfile)


if __name__ == "__main__":
    unittest.main()
