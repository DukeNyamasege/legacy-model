from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SessionRiskHardStopContractTests(unittest.TestCase):
    def test_worker_uses_account_session_profit_and_live_db_limits(self) -> None:
        source = (ROOT / "app" / "session_risk_stop_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("AccountRiskState", source)
        self.assertIn("ManagedAccount", source)
        self.assertIn("risk.session_profit", source)
        self.assertIn("account.take_profit", source)
        self.assertIn("account.stop_loss", source)
        self.assertNotIn('account_summary(account_id).get("profit")', source)
        self.assertIn('status="take_profit"', source)
        self.assertIn('status="stop_loss"', source)
        self.assertIn('"execution_stopped": True', source)
        self.assertIn('"next_start_fresh": True', source)

    def test_tp_and_sl_are_stopped_not_paused_and_keep_hit_value_until_next_start(self) -> None:
        lifecycle = (ROOT / "app" / "lifecycle_reset_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('_RISK_LIMIT_STOP_STATUSES = {"take_profit", "stop_loss"}', lifecycle)
        self.assertIn("STOPPED_STATUSES.update(_RISK_LIMIT_STOP_STATUSES)", lifecycle)
        self.assertIn("PAUSED_STATUSES.difference_update(_RISK_LIMIT_STOP_STATUSES)", lifecycle)
        self.assertIn('"session_profit": session_profit', lifecycle)
        self.assertIn('"limit_target": limit_target', lifecycle)
        self.assertIn('"limit_achieved": limit_achieved', lifecycle)
        self.assertIn('"risk_limit_is_hard_stop": status in _RISK_LIMIT_STOP_STATUSES', lifecycle)
        self.assertIn("fresh = requested_mode == \"start_again\" or previous in STOPPED_STATUSES", lifecycle)

    def test_custom_worker_installs_final_session_risk_authority(self) -> None:
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        self.assertIn("install_session_risk_stop_worker", worker)
        self.assertLess(
            worker.index("install_custom_strategy_runtime_lifecycle()"),
            worker.index("install_session_risk_stop_worker()"),
        )


class MultiDeviceAndFinalUIContractTests(unittest.TestCase):
    def test_starting_device_owns_run_and_other_devices_reload_server_strategy(self) -> None:
        source = (ROOT / "dashboard" / "final-dashboard-authority.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('event.target?.closest?.(\'[data-main-action="start"]\')', source)
        self.assertIn('getJSON("/me/trading-lifecycle")', source)
        self.assertIn('getJSON("/me/custom-strategy")', source)
        self.assertIn("SERVER_STRATEGY_PREFIX", source)
        self.assertIn("BUILDER_DRAFT_KEYS.forEach(storageRemove)", source)
        self.assertIn("window.location.reload()", source)
        self.assertIn("thisDeviceStarted", source)

    def test_trades_controls_are_two_equal_text_rectangles(self) -> None:
        css = (ROOT / "dashboard" / "final-dashboard-authority.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)", css)
        self.assertIn('content: "Clear Trades"', css)
        self.assertIn('content: "Stop Trading"', css)
        self.assertIn(".foa-reset-trades-icon svg", css)
        self.assertIn("display: none !important", css)

    def test_mobile_drawer_and_switch_follow_html_theme_and_bottom_account_layout(self) -> None:
        css = (ROOT / "dashboard" / "final-dashboard-authority.css").read_text(
            encoding="utf-8"
        )
        js = (ROOT / "dashboard" / "final-dashboard-authority.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('html[data-theme="light"] .foa-mobile-drawer', css)
        self.assertIn('html[data-theme="dark"] .foa-mobile-theme-toggle', css)
        self.assertIn('html[data-theme="light"] .foa-mobile-theme-toggle', css)
        self.assertIn('actions.before(account)', js)
        self.assertIn('nav.replaceChildren', js)
        self.assertIn('headCopy?.remove()', js)
        self.assertIn('[data-mobile-view="settings"]', js)

    def test_limit_notice_separates_target_from_achieved_session_pnl(self) -> None:
        source = (ROOT / "dashboard" / "final-dashboard-authority.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("limit_target", source)
        self.assertIn("limit_achieved", source)
        self.assertIn("Session P/L", source)
        self.assertIn("trading stopped", source)

    def test_final_assets_are_loaded_last_and_javascript_is_valid(self) -> None:
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn('./final-dashboard-authority.css', index)
        self.assertIn('./final-dashboard-authority.js', index)
        self.assertGreater(
            index.index('./final-dashboard-authority.css'),
            index.index('./trades-page-minimal.css'),
        )
        self.assertGreater(
            index.index('./final-dashboard-authority.js'),
            index.index('./trades-page-minimal.js'),
        )
        subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "final-dashboard-authority.js")],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
