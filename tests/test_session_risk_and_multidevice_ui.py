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


class VirtualHookParityAndRecoveryTests(unittest.TestCase):
    def test_virtual_hook_supports_every_custom_contract_family(self) -> None:
        source = (ROOT / "app" / "custom_virtual_contract_parity.py").read_text(
            encoding="utf-8"
        )
        for contract in (
            "DIGITOVER",
            "DIGITUNDER",
            "DIGITEVEN",
            "DIGITODD",
            "DIGITMATCH",
            "DIGITDIFF",
            "CALL",
            "PUT",
        ):
            self.assertIn(contract, source)
        self.assertIn("rf_repo._virtual_trade_outcome = _exact_virtual_outcome", source)
        self.assertIn("read_custom_strategy", source)
        self.assertIn("contract_for_config", source)

    def test_virtual_history_never_invents_over_three_or_over_four(self) -> None:
        source = (ROOT / "app" / "final_personal_trade_stream.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"contract_type": f"VIRTUAL HOOK · {contract_label}"', source)
        self.assertIn("virtual_contract_display", source)
        self.assertNotIn('row.barrier or "3"', source)
        self.assertNotIn("VIRTUAL OVER-4", source)
        self.assertNotIn("OVER-3 exact recovery", source)

    def test_runtime_faults_reconnect_without_disabling_enabled_account(self) -> None:
        recovery = (ROOT / "app" / "seamless_execution_recovery.py").read_text(
            encoding="utf-8"
        )
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        self.assertIn('"reconnecting"', recovery)
        self.assertIn("enabled_preserved=true", recovery)
        self.assertIn("_schedule_runtime_repair", recovery)
        self.assertIn("_drop_stale_execution_runtime", recovery)
        self.assertIn("preserve _custom_direct_virtual_due", recovery)
        self.assertNotIn("update_managed_account(int(managed_id), enabled=False)", recovery)
        self.assertGreater(
            worker.index("install_seamless_execution_recovery()"),
            worker.index("install_netlify_worker_bridge()"),
        )

    def test_all_markets_are_evaluated_from_the_symbol_set_not_one_pinned_market(self) -> None:
        source = (ROOT / "app" / "custom_strategy_direct_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("symbols.update(SUPPORTED_MARKETS)", source)
        self.assertIn("if symbol not in set(getattr(bot, \"symbols\", []) or []):", source)
        self.assertIn("_schedule_account_matches(bot, symbol=symbol, tick=tick)", source)
        self.assertIn("market_selected(item.config, symbol)", source)

    def test_all_even_and_all_odd_are_value_free_backend_comparators(self) -> None:
        source = (ROOT / "app" / "custom_strategy_comparator_extension.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"all_even"', source)
        self.assertIn('"all_odd"', source)
        self.assertIn('"value": None', source)
        self.assertIn("all(value % 2 == 0", source)
        self.assertIn("all(value % 2 == 1", source)


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

    def test_mobile_navigation_is_top_aligned_and_light_mode_readable(self) -> None:
        css = (ROOT / "dashboard" / "final-dashboard-authority.css").read_text(
            encoding="utf-8"
        )
        js = (ROOT / "dashboard" / "final-dashboard-authority.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("if (head && nav && head.nextElementSibling !== nav) head.after(nav)", js)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", css)
        self.assertIn('html[data-theme="light"] .foa-mobile-nav-button', css)
        self.assertIn("color: #0f172a !important", css)
        self.assertIn("margin: auto 0 0 !important", css)
        self.assertIn("nav.after(theme)", js)

    def test_trades_controls_are_two_equal_premium_text_rectangles(self) -> None:
        css = (ROOT / "dashboard" / "final-dashboard-authority.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)", css)
        self.assertIn('content: "Clear Trades"', css)
        self.assertIn('content: "Stop Trading"', css)
        self.assertIn("rgba(2, 6, 23, .98)", css)
        self.assertIn("rgba(127, 29, 29, .96)", css)
        self.assertIn("translateY(-2px)", css)
        self.assertIn("linear-gradient(110deg", css)

    def test_result_heading_and_values_share_exact_alignment(self) -> None:
        css = (ROOT / "dashboard" / "final-dashboard-authority.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".foa-trades-table .trade-head > :last-child", css)
        self.assertIn(".foa-trades-table .trade-row > :last-child", css)
        self.assertIn("justify-content: center !important", css)
        self.assertIn("text-align: center !important", css)

    def test_special_comparators_are_visible_without_value_and_saved_to_server(self) -> None:
        js = (ROOT / "dashboard" / "final-dashboard-authority.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('["all_even", "All even"]', js)
        self.assertIn('["all_odd", "All odd"]', js)
        self.assertIn("delete condition.value", js)
        self.assertIn("valueField.hidden = Boolean(special)", js)
        self.assertIn("Check last number of digits", js)
        self.assertIn("installSpecialComparatorRequestBridge", js)

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
