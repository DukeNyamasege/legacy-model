from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class MarketingTutorialAccountTests(unittest.TestCase):
    def test_marketing_split_is_not_installed_in_backend(self) -> None:
        source = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        self.assertNotIn("from app.marketing_tutorial_account import", source)
        self.assertNotIn("install_marketing_tutorial_account(app)", source)
        self.assertIn("Marketing/tutorial DOT/ROT splitting is presentation-only in the browser", source)
        self.assertIn("install_final_linked_accounts_6f2(app)", source)
        self.assertIn("install_vps_demo_balance_reset(app)", source)
        self.assertIn("install_vps_cross_device_runtime_sync(app)", source)

    def test_frontend_uses_exact_ui_pair_and_75_25_split(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn('const DOT_ID = "DOT93427967"', source)
        self.assertIn('const ROT_ID = "ROT92069206"', source)
        self.assertIn("const DOT_SHARE = 0.75", source)
        self.assertIn("const ROT_SHARE = 0.25", source)
        self.assertIn("provider - rot", source)

    def test_frontend_switch_is_visual_only_and_keeps_one_managed_account(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn("Both visible rows keep the same managed account ID", source)
        self.assertIn('rotRow.dataset.accountId = String(managedId())', source)
        self.assertIn('row.dataset.marketingView = selectedView', source)
        self.assertIn('window.addEventListener("click"', source)
        self.assertNotIn('fetch("/api/me/switch-account"', source)
        self.assertNotIn('fetch("/me/switch-account"', source)

    def test_provider_balance_events_change_only_opening_visual_partition(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn("const delta = provider - ledger.provider", source)
        self.assertIn('if (targetView === "rot") ledger.rot = roundMoney(ledger.rot + delta)', source)
        self.assertIn('else ledger.dot = roundMoney(ledger.dot + delta)', source)
        self.assertIn("ledger.provider = roundMoney(provider)", source)
        self.assertIn("detail.balance = visibleBalance(ledger)", source)
        self.assertIn("rememberContractOwner(contractId, view())", source)
        self.assertIn("movementView(detail)", source)
        self.assertNotIn("absolute * ROT_SHARE", source)
        self.assertNotIn("absolute * DOT_SHARE", source)

    def test_standard_deriv_execution_is_not_wrapped_or_guarded_by_marketing_ui(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertNotIn("WebSocket.prototype.send =", source)
        self.assertNotIn("guardedDemoPartitionSend", source)
        self.assertNotIn("demo partition insufficient", source)
        self.assertIn("The backend and Deriv still receive/use the real provider balance", source)

    def test_demo_reset_resplits_ui_only(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn('window.addEventListener("derivadmin:demo-balance-reset"', source)
        self.assertIn("const ledger = splitReset(provider)", source)
        self.assertIn("detail.balance = visibleBalance(ledger)", source)
        self.assertIn("writeOwners({})", source)

    def test_final_production_layer_removes_banner_and_restores_lower_stats(self) -> None:
        source = (ROOT / "scripts" / "finalize-marketing-ui-layout-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("Run totals restored below tab content", source)
        self.assertIn("tutorial implementation badge removed", source)
        self.assertIn("run panel totals are not below tab content", source)
        self.assertIn('document.querySelectorAll(".marketing-tutorial-runtime-badge")', source)
        self.assertIn("display_balance: () => displayBalance()", source)
        self.assertIn("marketingUi.display_balance?.()", source)
        self.assertIn("if (row.dataset.marketingView) return;", source)
        self.assertIn("backend and Deriv execution path unchanged", source)

    def test_marketing_layout_finalizer_runs_after_history_finalizer(self) -> None:
        source = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
        copy_history = source.index("COPY scripts/finalize-history-preload-runpanel-v1.mjs")
        copy_layout = source.index("COPY scripts/finalize-marketing-ui-layout-v1.mjs")
        run_history = source.rindex("node scripts/finalize-history-preload-runpanel-v1.mjs")
        run_layout = source.rindex("node scripts/finalize-marketing-ui-layout-v1.mjs")
        self.assertLess(copy_history, copy_layout)
        self.assertLess(run_history, run_layout)
        self.assertIn("node --check dist/direct-runtime-ux-v4.js", source)
        self.assertIn("node --check dist/direct-demo-reset-router-v1.js", source)

    def test_frontend_asset_is_cache_busted_in_final_production_layer(self) -> None:
        source = (ROOT / "scripts" / "finalize-marketing-ui-layout-v1.mjs").read_text(encoding="utf-8")
        self.assertIn("20260821-marketing-ui-v5", source)
        self.assertIn("20260821-marketing-balance-v7", source)
        self.assertIn("20260821-runpanel-bottom-v2", source)


if __name__ == "__main__":
    unittest.main()
