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
        for protected_path in (
            "/me",
            "/me/accounts",
            "/me/switch-account",
            "/me/reset-demo-balance",
            "/me/direct-execution/bootstrap",
            "/me/direct-execution/arm",
            "/me/direct-execution/receipt",
        ):
            self.assertIn(protected_path, source)

    def test_frontend_uses_exact_ui_pair_and_75_25_split(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn('const DOT_ID = "DOT93427967"', source)
        self.assertIn('const ROT_ID = "ROT92069206"', source)
        self.assertIn("const DOT_SHARE = 0.75", source)
        self.assertIn("const ROT_SHARE = 0.25", source)
        self.assertIn("provider - rot", source)
        self.assertIn("UI split · DOT 75% · ROT 25%", source)

    def test_frontend_switch_is_visual_only_and_keeps_one_managed_account(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn("Both visible rows keep the same managed account ID", source)
        self.assertIn('rotRow.dataset.accountId = String(managedId())', source)
        self.assertIn('row.dataset.marketingView = selectedView', source)
        self.assertIn('window.addEventListener("click"', source)
        self.assertNotIn('fetch("/api/me/switch-account"', source)
        self.assertNotIn('fetch("/me/switch-account"', source)

    def test_provider_balance_events_change_only_selected_visual_partition(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn("const delta = provider - ledger.provider", source)
        self.assertIn('if (view() === "rot") ledger.rot = roundMoney(ledger.rot + delta)', source)
        self.assertIn('else ledger.dot = roundMoney(ledger.dot + delta)', source)
        self.assertIn("ledger.provider = roundMoney(provider)", source)
        self.assertIn("detail.balance = visibleBalance(ledger)", source)
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

    def test_frontend_asset_is_cache_busted(self) -> None:
        source = (ROOT / "scripts" / "inject-frontend-assets.mjs").read_text(encoding="utf-8")
        self.assertIn(
            '["direct-demo-reset-router-v1.js", "20260821-marketing-dot-rot-v4-ui-only"]',
            source,
        )


if __name__ == "__main__":
    unittest.main()
