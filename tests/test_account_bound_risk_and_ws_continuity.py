from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AccountBoundRiskAndWsContinuityTests(unittest.TestCase):
    def test_risk_notice_requires_current_account_state_and_exact_target(self) -> None:
        source = (ROOT / "dashboard" / "account-bound-risk-notice.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('const me = await getJSON("/me")', source)
        self.assertIn('getJSON("/me/trading-lifecycle")', source)
        self.assertIn("if (meStatus !== status) return false", source)
        self.assertIn("if (Boolean(me.enabled) || Boolean(lifecycle.enabled)) return false", source)
        self.assertIn("lifecycle.risk_limit_is_hard_stop !== true", source)
        self.assertIn("Math.abs(Math.abs(configured) - targetMagnitude) > 0.005", source)
        self.assertIn("[data-mode]", source)
        self.assertIn("invalidateForAccountSwitch", source)

    def test_legacy_limit_notices_are_never_allowed_to_surface(self) -> None:
        css = (ROOT / "dashboard" / "account-bound-risk-notice.css").read_text(
            encoding="utf-8"
        )
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn(".limit-notifier", css)
        self.assertIn("display: none !important", css)
        self.assertIn("account-bound-risk-notice.css", index)
        self.assertIn("account-bound-risk-notice.js", index)
        self.assertGreater(
            index.index("account-bound-risk-notice.js"),
            index.index("signed-risk-limit-display.js"),
        )

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

    def test_continuity_installs_after_martingale_authority(self) -> None:
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(
            encoding="utf-8"
        )
        martingale = worker.rindex("install_manual_martingale_execution_authority()")
        continuity = worker.rindex("install_final_execution_continuity()")
        self.assertLess(martingale, continuity)
        self.assertIn(
            "runtime_fault_policy=soft_reconnect_no_forced_disconnect",
            worker,
        )


if __name__ == "__main__":
    unittest.main()
