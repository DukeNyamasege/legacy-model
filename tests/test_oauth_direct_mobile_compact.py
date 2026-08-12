from __future__ import annotations

import unittest
from pathlib import Path

from app.oauth_client import normalize_token_payload


ROOT = Path(__file__).resolve().parents[1]


class OAuthDirectCredentialContractTests(unittest.TestCase):
    def test_oauth_token_without_scope_response_keeps_requested_trade_scope(self) -> None:
        payload = normalize_token_payload(
            {
                "access_token": "oauth-access-for-test",
                "refresh_token": "oauth-refresh-for-test",
                "expires_in": 3600,
            }
        )
        self.assertEqual(payload["auth_type"], "oauth")
        self.assertIn("trade", str(payload["scope"]).split())
        self.assertIn("application_read", str(payload["scope"]).split())

    def test_account_runtime_prefers_oauth_trade_credential_without_manual_pat(self) -> None:
        source = (ROOT / "app" / "personal_autotrade_start_fix.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('if auth_type == "oauth":', source)
        self.assertIn('"trade" in _token_scopes(payload)', source)
        self.assertIn("base_api.has_trading_api_token = _has_account_purchase_token", source)

        bot = (ROOT / "enhanced_bot.py").read_text(encoding="utf-8")
        self.assertIn("OAuth access tokens are valid for that flow", bot)
        self.assertIn('return oauth_token if oauth_token and "trade" in scopes else ""', bot)


class MobileFirstCompactBuilderContractTests(unittest.TestCase):
    def test_phone_kpis_and_trade_controls_remain_horizontal(self) -> None:
        css = (ROOT / "dashboard" / "mobile-first-compact.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("grid-template-columns: repeat(5, minmax(112px, 1fr)) !important", css)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr)) !important", css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr)) !important", css)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr)) !important", css)
        self.assertIn("overflow-x: auto !important", css)
        self.assertIn("min-width: 560px !important", css)
        self.assertIn("@media (max-width: 420px)", css)

    def test_money_management_remains_one_compact_horizontal_band(self) -> None:
        css = (ROOT / "dashboard" / "mobile-first-compact.css").read_text(
            encoding="utf-8"
        )
        money = css.split(".money-grid {", 1)[1].split("}", 1)[0]
        self.assertIn("repeat(5", money)
        self.assertIn("overflow-x: auto", money)
        two_col = css.split(".builder-two-col {", 1)[1].split("}", 1)[0]
        self.assertIn("minmax(170px", two_col)
        self.assertIn("minmax(520px", two_col)

    def test_final_asset_route_appends_mobile_override_after_base_css(self) -> None:
        authority = (ROOT / "app" / "builder_first_dashboard_authority.py").read_text(
            encoding="utf-8"
        )
        css_block = authority.split("def builder_first_css()", 1)[1].split(
            "def builder_first_dashboard_js()", 1
        )[0]
        self.assertLess(css_block.index('"dashboard-v2.css"'), css_block.index('"mobile-first-compact.css"'))
        js_block = authority.split("def builder_first_dashboard_js()", 1)[1].split(
            "def builder_first_actions_js()", 1
        )[0]
        self.assertLess(js_block.index('"dashboard-v2.js"'), js_block.index('"oauth-direct-runtime.js"'))

    def test_settings_reconnects_oauth_instead_of_requesting_manual_api_token(self) -> None:
        source = (ROOT / "dashboard" / "oauth-direct-runtime.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("No separate API token is required", source)
        self.assertIn('href="/oauth/start"', source)
        self.assertIn('card.querySelector("#token-form")', source)
        self.assertIn("form.replaceWith(replacement)", source)
        self.assertNotIn('name="api_token"', source)


if __name__ == "__main__":
    unittest.main()
