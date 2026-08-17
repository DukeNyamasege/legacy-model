from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AccountBoundRiskAndWsContinuityTests(unittest.TestCase):
    def test_account_bound_risk_contract_is_backend_authoritative(self) -> None:
        source = (ROOT / "app" / "session_risk_api_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('@app.get("/me/trading-lifecycle")', source)
        self.assertIn("managed_account_id", source)
        self.assertIn("account_id_masked", source)
        self.assertIn("execution_status_updated_at", source)
        self.assertIn("read_session_risk_limits", source)
        self.assertIn("limit_target", source)
        self.assertIn("limit_achieved", source)
        self.assertIn('"risk_limit_is_hard_stop": status in {"take_profit", "stop_loss"}', source)
        self.assertIn("session_limits_started_at", source)
        self.assertIn("if not enabled and status not in", source)

    def test_retired_risk_notice_ui_is_physically_removed_from_6f3(self) -> None:
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        final_ui = (ROOT / "dashboard" / "final-ui-shell-v2.js").read_text(
            encoding="utf-8"
        )
        premium = (ROOT / "dashboard" / "final-premium-6f3.js").read_text(
            encoding="utf-8"
        )
        self.assertFalse((ROOT / "dashboard" / "account-bound-risk-notice.js").exists())
        self.assertFalse((ROOT / "dashboard" / "account-bound-risk-notice.css").exists())
        self.assertNotIn("account-bound-risk-notice", index)
        self.assertNotIn("account-bound-risk-notice", final_ui)
        self.assertNotIn("account-bound-risk-notice", premium)
        self.assertNotIn("signed-risk-limit-display.js", index)
        self.assertNotIn("limit-notifier", final_ui)
        self.assertIn("final-ui-shell-v2", index)
        self.assertIn("final-premium-6f3", index)
        self.assertNotIn("final-ui-shell-v1", index)

    def test_final_execution_continuity_never_forces_websocket_close(self) -> None:
        source = (ROOT / "app" / "final_execution_continuity.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_schedule_private_reconnect", source)
        self.assertNotIn("websocket.close", source)
        self.assertNotIn("ws.close", source)
        self.assertIn("private_ws.wake_private_connection", source)
        self.assertIn("seamless._schedule_runtime_repair", source)
        self.assertIn("forced_disconnect=false", source)
        self.assertIn("lifecycle_stop=false", source)

    def test_ordinary_reconnect_is_fast_but_rate_limit_backoff_remains_separate(self) -> None:
        source = (ROOT / "app" / "final_execution_continuity.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("PRIVATE_WS_NORMAL_RECONNECT_BASE_SECONDS", source)
        self.assertIn("PRIVATE_WS_NORMAL_RECONNECT_MAX_SECONDS", source)
        self.assertIn("private_ws._normal_backoff = _continuity_backoff", source)
        self.assertNotIn("private_ws._rate_backoff =", source)

    def test_continuity_and_final_consistency_install_after_martingale_authority(self) -> None:
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(
            encoding="utf-8"
        )
        martingale = worker.rindex("install_manual_martingale_execution_authority()")
        continuity = worker.rindex("install_final_execution_continuity()")
        consistency = worker.rindex("install_custom_execution_consistency_authority()")
        self.assertLess(martingale, continuity)
        self.assertLess(continuity, consistency)
        self.assertIn(
            "runtime_fault_policy=reconnect_reconcile_never_stop",
            worker,
        )
        self.assertIn(
            "ambiguous_buy_policy=reconcile_before_next_real",
            worker,
        )


if __name__ == "__main__":
    unittest.main()
