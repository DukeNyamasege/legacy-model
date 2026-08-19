from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExecutionStopReasonAuthorityTests(unittest.TestCase):
    def test_worker_installs_reason_and_stampede_authorities_last(self) -> None:
        source = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        final_continuity = source.index("install_final_execution_continuity()")
        reason_authority = source.index("install_execution_stop_reason_authority()")
        instant_start = source.index("install_custom_strategy_instant_start()")
        stampede_guard = source.index("install_custom_strategy_connection_stampede_guard()")
        self.assertLess(final_continuity, reason_authority)
        self.assertLess(reason_authority, instant_start)
        self.assertLess(instant_start, stampede_guard)
        self.assertIn("stop_reason_authority=durable", source)
        self.assertIn("execution_liveness_watchdog=browser_aware", source)
        self.assertIn("connection_repair=targeted_singleflight", source)
        self.assertIn("sibling_wake=false", source)
        self.assertIn("global_revalidation=false", source)

    def test_terminal_causes_cannot_be_downgraded_to_generic_stopped(self) -> None:
        source = (ROOT / "app" / "execution_stop_reason_authority.py").read_text(
            encoding="utf-8"
        )
        for status in (
            "credential_error",
            "invalid_account",
            "token_required",
            "contract_unavailable",
            "purchase_registration_error",
            "insufficient_balance",
            "purchase_insufficient_balance",
            "take_profit",
            "stop_loss",
        ):
            self.assertIn(f'"{status}"', source)
        self.assertIn("requested in _GENERIC_STOP_STATUSES", source)
        self.assertIn("current in _ACTIONABLE_TERMINAL_STATUSES", source)
        self.assertIn("ACCOUNT_STOP_REASON_PRESERVED", source)
        self.assertIn("row.execution_status_reason = _safe_reason", source)

    def test_automatic_failure_stop_is_durable_and_reasoned(self) -> None:
        source = (ROOT / "app" / "execution_stop_reason_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def _write_terminal_state", source)
        self.assertIn("row.enabled = False", source)
        self.assertIn("row.execution_status = normalized", source)
        self.assertIn("row.execution_status_reason = safe_reason", source)
        self.assertIn("ACCOUNT_AUTOTRADE_STOP_RECORDED", source)
        self.assertIn("_write_terminal_state(", source)
        self.assertIn('"error",', source)

    def test_connection_watchdog_is_observational_not_a_reconnect_storm(self) -> None:
        guard = (ROOT / "app" / "custom_strategy_connection_stampede_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("async def _singleflight_liveness_watchdog", guard)
        self.assertIn("ACCOUNT_PRIVATE_RECONNECT_OWNED", guard)
        self.assertIn("watchdog_wake=false", guard)
        self.assertIn("session_task_alive=true", guard)
        self.assertIn("_schedule_targeted_runtime_repair", guard)
        self.assertNotIn("await bot.validate_accounts()", guard)
        self.assertNotIn("await self.validate_accounts()", guard)

    def test_runtime_repair_is_per_account_singleflight(self) -> None:
        guard = (ROOT / "app" / "custom_strategy_connection_stampede_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_custom_targeted_repair_tasks", guard)
        self.assertIn("if current is not None and not current.done():", guard)
        self.assertIn("CUSTOM_TARGETED_RUNTIME_REPAIR", guard)
        self.assertIn("global_validation=false", guard)
        self.assertIn("sibling_sessions_rebuilt=false", guard)
        self.assertIn(
            "seamless._schedule_runtime_repair = _schedule_targeted_runtime_repair",
            guard,
        )

    def test_fresh_start_wakes_only_the_started_account(self) -> None:
        guard = (ROOT / "app" / "custom_strategy_connection_stampede_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('if status == "starting":', guard)
        self.assertIn("_admit_one_runtime_account(self, managed_id)", guard)
        self.assertIn("_ensure_one_private_session(self, managed_id, wake=True)", guard)
        self.assertIn("CUSTOM_TARGETED_START_WAKEUP", guard)
        self.assertIn("sibling_sessions_woken=false", guard)
        self.assertIn("CUSTOM_TARGETED_START_PICKUP", guard)

    def test_otp_bootstrap_timeout_is_background_safe_not_five_second_storm(self) -> None:
        guard = (ROOT / "app" / "custom_strategy_connection_stampede_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_OTP_BOOTSTRAP_TIMEOUT_SECONDS = 15.0", guard)
        self.assertIn(
            "instant._STARTUP_REST_TIMEOUT_SECONDS = _OTP_BOOTSTRAP_TIMEOUT_SECONDS",
            guard,
        )

    def test_final_ui_reads_durable_lifecycle_without_retired_banner(self) -> None:
        reason = (ROOT / "app" / "execution_stop_reason_authority.py").read_text(
            encoding="utf-8"
        )
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        shell = (ROOT / "dashboard" / "final-ui-shell-v2.js").read_text(
            encoding="utf-8"
        )
        premium = (ROOT / "dashboard" / "final-premium-6f3.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("ACCOUNT_STOP_REASON_PRESERVED", reason)
        self.assertIn("take_profit", reason)
        self.assertIn("stop_loss", reason)
        self.assertFalse((ROOT / "dashboard" / "execution-status-banner.js").exists())
        self.assertNotIn("execution-status-banner.js", index)
        self.assertIn('json("/me/trading-lifecycle")', shell)
        self.assertIn("state.lifecycle", shell)
        self.assertIn("state.lifecycle?.lifecycle", shell)
        self.assertNotIn('<script src="/final-ui-shell-v2.js?v=20260817-6f2-1" defer>', index)
        self.assertIn('/final-ui-shell-v2.js?v=20260819-block-workspace-v13', premium)
        self.assertNotIn('/final-ui-shell-v1.js', index)


if __name__ == "__main__":
    unittest.main()
