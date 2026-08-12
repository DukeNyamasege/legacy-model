from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MinimalTradesPageContractTests(unittest.TestCase):
    def test_trades_page_is_reduced_to_kpis_columns_rows_and_one_control_bar(self) -> None:
        source = (ROOT / "dashboard" / "trades-page-minimal.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "trades-page-minimal.css").read_text(encoding="utf-8")
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")

        self.assertIn('["Time", "Market", "Trade type", "Stake", "Exit spot", "Result"]', source)
        self.assertIn('main.querySelector(".trades-control-panel")?.remove()', source)
        self.assertIn('footer.id = "foa-trades-controls-footer"', source)
        self.assertIn('footer.className = "foa-trades-controls-footer"', source)
        self.assertIn('resetButton.className = "foa-reset-trades-icon"', source)
        self.assertIn('button.className = "foa-stop-trades-icon"', source)
        self.assertIn('panel.className = "builder-panel foa-trades-table"', source)
        self.assertIn('fetch("/me/trades/today"', source)
        self.assertIn("mergeRows(fullRows, live)", source)
        self.assertNotIn("Recent activity", source)
        self.assertNotIn("Recent Trades", source)
        self.assertNotIn("View all", source)
        self.assertNotIn("Session tracking", source)
        self.assertNotIn("Local Trade History", source)

        # The canonical footer lookup and duplicate removal prevent the old
        # MutationObserver loop from creating another reset icon on every render.
        self.assertIn('main.querySelector("#foa-trades-controls-footer")', source)
        self.assertIn("if (footer && node !== footer) node.remove()", source)
        self.assertIn("if (main.lastElementChild !== controls) main.appendChild(controls)", source)

        self.assertIn("body.foa-trades-page-active main", css)
        self.assertIn("margin-top: 0 !important", css)
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr))", css)
        self.assertIn(".foa-trades-controls-footer", css)
        self.assertIn(".foa-reset-trades-icon", css)
        self.assertIn(".foa-stop-trades-icon", css)
        self.assertIn("width: 32px", css)
        self.assertIn("body.foa-trades-page-active .foa-mobile-menu-launcher.foa-mobile-execution-topbar", css)
        self.assertIn("margin-bottom: 0 !important", css)
        self.assertIn('./trades-page-minimal.css', index)
        self.assertIn('./trades-page-minimal.js', index)

    def test_trades_page_stop_control_calls_same_account_stop_route(self) -> None:
        source = (ROOT / "dashboard" / "trades-page-minimal.js").read_text(encoding="utf-8")

        self.assertIn('async function stopTradingFromTrades(button)', source)
        self.assertIn('fetch("/me/stop-trading"', source)
        self.assertIn('method: "POST"', source)
        self.assertIn('credentials: "same-origin"', source)
        self.assertIn('button.setAttribute("aria-label", "Stop auto trading")', source)
        self.assertIn('button.dataset.stopTrades = "true"', source)

    def test_reset_uses_one_triangle_style_icon_not_a_trash_can(self) -> None:
        source = (ROOT / "dashboard" / "trades-page-minimal.js").read_text(encoding="utf-8")

        self.assertIn("function resetIconMarkup()", source)
        self.assertIn('d="M5 7h7V3l5 5-5 5V9H7', source)
        self.assertNotIn('d="M4 7h16"', source)
        self.assertIn('resetButton.setAttribute("aria-label", "Reset trade view")', source)

    def test_settings_navigation_is_removed_and_auth_action_is_a_toggle(self) -> None:
        source = (ROOT / "dashboard" / "trades-page-minimal.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "trades-page-minimal.css").read_text(encoding="utf-8")

        self.assertIn("removeSettingsNavigation()", source)
        self.assertIn("settings?.remove()", source)
        self.assertIn('document.querySelectorAll(\'[data-mobile-view="settings"]\')', source)
        self.assertIn("enforceLoginLogoutToggle()", source)
        self.assertIn("node.hidden = !authenticated", source)
        self.assertIn("node.hidden = authenticated", source)
        self.assertIn("[data-mobile-login][hidden]", css)
        self.assertIn("[data-mobile-logout][hidden]", css)

    def test_start_auto_trade_redirects_to_trades_only_after_running_state(self) -> None:
        source = (ROOT / "dashboard" / "trades-page-minimal.js").read_text(encoding="utf-8")

        self.assertIn('event.target?.closest?.(\'[data-main-action="start"]\')', source)
        self.assertIn("startRedirectPending = true", source)
        self.assertIn('document.querySelector(\'[data-main-action="stop"]\')', source)
        self.assertIn('document.querySelector(\'.builder-header [data-view="trades"]\')', source)
        self.assertIn("trades.click()", source)

        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "trades-page-minimal.js")],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()