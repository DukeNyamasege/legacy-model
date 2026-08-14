from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExecutionStopReasonAuthorityTests(unittest.TestCase):
    def test_worker_installs_reason_authority_last(self) -> None:
        source = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        final_continuity = source.index("install_final_execution_continuity()")
        reason_authority = source.index("install_execution_stop_reason_authority()")
        self.assertLess(final_continuity, reason_authority)
        self.assertIn("stop_reason_authority=durable", source)
        self.assertIn("execution_liveness_watchdog=true", source)

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
        self.assertIn('row.execution_status = normalized', source)
        self.assertIn('row.execution_status_reason = safe_reason', source)
        self.assertIn("ACCOUNT_AUTOTRADE_STOP_RECORDED", source)
        self.assertIn("_write_terminal_state(", source)
        self.assertIn('"error",', source)

    def test_started_account_missing_session_is_repaired_not_stopped(self) -> None:
        source = (ROOT / "app" / "execution_stop_reason_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("async def _execution_liveness_watchdog", source)
        self.assertIn("_private_session_for_account", source)
        self.assertIn("private_ws.wake_private_connection(session)", source)
        self.assertIn("seamless._schedule_runtime_repair(bot, managed_id)", source)
        self.assertIn('"reconnecting",', source)
        self.assertIn("Auto Trading remains active", source)
        self.assertIn("ACCOUNT_EXECUTION_LIVENESS_REPAIR", source)
        self.assertIn("lifecycle_stop=false auto_retry=true", source)

    def test_dashboard_surfaces_reasoned_terminal_states(self) -> None:
        source = (ROOT / "dashboard" / "execution-status-banner.js").read_text(
            encoding="utf-8"
        )
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn("REASONED_TERMINAL", source)
        self.assertIn('runtimeState === "STOPPED"', source)
        self.assertIn('runtimeState === "ERROR"', source)
        self.assertIn('box.setAttribute("role", "alert")', source)
        self.assertIn('box.setAttribute("aria-live", "assertive")', source)
        self.assertIn("Take profit stop", source)
        self.assertIn("Stop loss stop", source)
        self.assertIn("/execution-status-banner.js?v=20260814-1", index)


if __name__ == "__main__":
    unittest.main()
