from __future__ import annotations

import subprocess
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
    def test_header_scrolls_away_and_execution_kpis_are_sticky(self) -> None:
        css = (ROOT / "dashboard" / "mobile-first-compact.css").read_text(
            encoding="utf-8"
        )
        header = css.split(".builder-header {", 1)[1].split("}", 1)[0]
        self.assertIn("position: relative !important", header)
        self.assertIn("top: auto !important", header)

        stats = css.split(".builder-stats,", 1)[1].split("}", 1)[0]
        self.assertIn("position: sticky", stats)
        self.assertIn("env(safe-area-inset-top)", stats)

    def test_phone_builder_wraps_inside_viewport_without_horizontal_scrollers(self) -> None:
        css = (ROOT / "dashboard" / "mobile-first-compact.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("overflow-x: clip !important", css)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr)) !important", css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr)) !important", css)
        self.assertIn("grid-template-columns: 1fr !important", css)
        self.assertNotIn("overflow-x: auto !important", css)
        self.assertNotIn("min-width: 560px !important", css)
        self.assertNotIn("minmax(520px", css)
        self.assertIn("@media (max-width: 420px)", css)
        self.assertIn("@media (max-width: 360px)", css)

    def test_money_and_condition_controls_wrap_to_new_rows(self) -> None:
        css = (ROOT / "dashboard" / "mobile-first-compact.css").read_text(
            encoding="utf-8"
        )
        money = css.split(".money-grid {", 1)[1].split("}", 1)[0]
        self.assertIn("repeat(2, minmax(0, 1fr))", money)
        self.assertIn("overflow: visible", money)

        two_col = css.split(".builder-two-col {", 1)[1].split("}", 1)[0]
        self.assertIn("grid-template-columns: 1fr", two_col)
        self.assertIn("overflow: visible", two_col)

        conditions = css.split("/* Conditions wrap into two columns.", 1)[1]
        rules = conditions.split(".rule-card,", 1)[1].split("}", 1)[0]
        self.assertIn("repeat(2, minmax(0, 1fr))", rules)
        self.assertIn("min-width: 0", rules)

    def test_mobile_trade_history_remains_left_to_right_without_side_scroll(self) -> None:
        css = (ROOT / "dashboard" / "mobile-first-compact.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".trade-head,\n  .trade-row {", css)
        trades = css.split(".trade-head,\n  .trade-row {", 1)[1].split("}", 1)[0]
        self.assertIn("minmax(0, .82fr)", trades)
        self.assertIn("minmax(0, 1.24fr)", trades)
        self.assertIn("width: 100% !important", trades)
        self.assertIn("min-width: 0 !important", trades)

        base = (ROOT / "dashboard" / "dashboard-v2.css").read_text(encoding="utf-8")
        mobile = base.split("@media (max-width: 760px)", 1)[1]
        self.assertIn(".trade-head {\n    display: none;", mobile)
        # The final mobile layer intentionally overrides the old hidden header and
        # one-column trade card after the base stylesheet is emitted.
        self.assertIn("display: grid !important", trades)

    def test_live_realtime_client_preserves_nonzero_metrics_across_empty_refresh(self) -> None:
        source = (ROOT / "dashboard" / "netlify-realtime-client.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('const METRIC_CACHE_PREFIX = "foa-live-metrics-v2"', source)
        self.assertIn("function stableMetrics(me, trades)", source)
        self.assertIn("incomingIsEmpty && cachedHasActivity", source)
        self.assertIn("function snapshotWithStableTrades(snapshot)", source)
        self.assertIn("function ensureTradeBalanceStat(me)", source)
        self.assertIn('label.textContent = "Balance"', source)
        self.assertIn("applySnapshot(lastSnapshot);", source)
        self.assertNotIn("requestAnimationFrame(() => applySnapshot(lastSnapshot))", source)

    def test_mobile_header_is_replaced_by_left_drawer(self) -> None:
        css = (ROOT / "dashboard" / "mobile-menu-exit-spot.css").read_text(
            encoding="utf-8"
        )
        source = (ROOT / "dashboard" / "mobile-menu-exit-spot.js").read_text(
            encoding="utf-8"
        )
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn(".builder-header {\n    display: none !important;", css)
        self.assertIn("transform: translateX(-104%)", css)
        self.assertIn("inset: 0 auto 0 0", css)
        self.assertIn("foa-mobile-menu-button", source)
        self.assertIn('data-mobile-view="main"', source)
        self.assertIn('data-mobile-view="settings"', source)
        self.assertIn('data-mobile-view="trades"', source)
        self.assertIn("data-mobile-theme-toggle", source)
        self.assertIn("data-mobile-risk", source)
        self.assertIn("data-mobile-logout", source)
        self.assertIn("currentAccountLabel()", source)
        self.assertIn("reorderTradeStats()", source)
        self.assertIn('./mobile-menu-exit-spot.css', index)
        self.assertIn('./mobile-menu-exit-spot.js', index)

        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "mobile-menu-exit-spot.js")],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_exit_spot_column_uses_settlement_digit_without_backend_changes(self) -> None:
        source = (ROOT / "dashboard" / "mobile-menu-exit-spot.js").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "dashboard" / "mobile-menu-exit-spot.css").read_text(
            encoding="utf-8"
        )
        backend = (ROOT / "app" / "final_public_controls.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('label.textContent = "Exit spot"', source)
        self.assertIn("row.exit_digit ?? row.actual_last_digit", source)
        self.assertIn("row.exit_spot ?? row.exit_tick", source)
        self.assertIn("trade-exit-spot", source)
        self.assertIn("trade-exit-spot", css)
        self.assertIn('"exit_spot": trade.exit_tick', backend)
        self.assertIn('"exit_digit": trade.exit_digit', backend)
        self.assertIn("minmax(0, .62fr)", css)

    def test_mobile_execution_header_is_one_menu_plus_five_stat_row(self) -> None:
        css = (ROOT / "dashboard" / "mobile-topbar-compact.css").read_text(
            encoding="utf-8"
        )
        source = (ROOT / "dashboard" / "mobile-topbar-compact.js").read_text(
            encoding="utf-8"
        )
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: 42px minmax(0, 1fr) !important", css)
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr)) !important", css)
        self.assertIn("height: 44px !important", css)
        self.assertIn("position: sticky !important", css)
        self.assertIn(".builder-stats,\n  .builder-stats.compact {\n    display: none !important;", css)
        self.assertIn(".trades-control-panel {\n    display: none !important;", css)
        self.assertIn('["balance", "Balance"]', source)
        self.assertIn('["runs", "Runs"]', source)
        self.assertIn('["profit", "P/L"]', source)
        self.assertIn('["wins", "Wins"]', source)
        self.assertIn('["losses", "Losses"]', source)
        self.assertLess(source.index('["balance", "Balance"]'), source.index('["runs", "Runs"]'))
        self.assertLess(source.index('["runs", "Runs"]'), source.index('["profit", "P/L"]'))
        self.assertLess(source.index('["profit", "P/L"]'), source.index('["wins", "Wins"]'))
        self.assertLess(source.index('["wins", "Wins"]'), source.index('["losses", "Losses"]'))
        self.assertIn('launcher.classList.add("foa-mobile-execution-topbar")', source)
        self.assertIn("window.FOA_NETLIFY_LIVE_CACHE", source)
        self.assertIn('./mobile-topbar-compact.css', index)
        self.assertIn('./mobile-topbar-compact.js', index)
        self.assertLess(index.index('./mobile-menu-exit-spot.css'), index.index('./mobile-topbar-compact.css'))
        self.assertLess(index.index('./mobile-menu-exit-spot.js'), index.index('./mobile-topbar-compact.js'))

        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "mobile-topbar-compact.js")],
            check=True,
            capture_output=True,
            text=True,
        )

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
