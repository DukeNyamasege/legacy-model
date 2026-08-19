from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RunPanelLedgerV10Contract(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_browser_and_server_rows_share_one_retained_snapshot_without_timer(self) -> None:
        ledger = self.text("dashboard/direct-transaction-ledger-v6.js")
        self.assertIn('explicit !== null && explicit !== undefined && explicit !== ""', ledger)
        self.assertNotIn("const explicitDigit = Number(explicit);", ledger)
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
        self.assertNotIn("margin-bottom:64px!important", ux)
        self.assertIn(".top-account-switch .account-switch-summary", ux)
        self.assertIn("grid-template-columns:auto auto minmax(0,1fr) auto", ux)
        self.assertIn("text-align:center!important", ux)
        self.assertIn("text-overflow:clip!important", ux)

    def test_frontend_cache_busts_continuity_v10_assets(self) -> None:
        dockerfile = self.text("Dockerfile.frontend")
        assets = self.text("scripts/inject-frontend-assets.mjs")
        self.assertIn('["direct-transaction-ledger-v6.js", "20260819-provider-ledger-v11"]', assets)
        self.assertIn('["direct-run-panel-authority-v6.js", "20260819-single-run-panel-v3"]', assets)
        self.assertIn('["run-panel-usability-v1.js", "20260819-summary-clear-v3"]', assets)
        self.assertIn("node scripts/finalize-execution-continuity-v1.mjs", dockerfile)
        self.assertIn("node scripts/finalize-global-recovery-v1.mjs", dockerfile)
        self.assertIn("node --check dist/direct-transaction-ledger-v6.js", dockerfile)
        self.assertIn("node --check dist/direct-run-panel-authority-v6.js", dockerfile)
        self.assertIn("node scripts/inject-frontend-assets.mjs", dockerfile)

    def test_run_panel_uses_brighter_readable_dashboard_theme(self) -> None:
        run_panel = self.text("dashboard/direct-run-panel-authority-v6.js")
        theme = self.text("dashboard/tutorial-camera-theme-v1.css")
        self.assertIn("font-family:Inter,Segoe UI,Roboto,Arial,sans-serif", run_panel)
        self.assertIn("background:#061526", run_panel)
        self.assertIn("color:#f4fbff", run_panel)
        self.assertIn("color:#ffffff!important;font-weight:800", run_panel)
        self.assertIn("letter-spacing:0", run_panel)
        self.assertIn("Canonical run summary: one theme authority", theme)
        self.assertIn("background: #102b44", theme)
        self.assertIn("color: #b9d7ee", theme)
        self.assertIn("Canonical light transaction palette", theme)
        self.assertIn('html[data-theme="light"] .global-run-panel.global-run-panel .transaction-head-v6', theme)
        self.assertIn("background: #e4f0f8", theme)
        self.assertIn('html[data-theme="light"] .global-run-panel.global-run-panel .run-panel-stats', theme)


if __name__ == "__main__":
    unittest.main()
