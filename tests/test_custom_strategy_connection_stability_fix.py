from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CustomStrategyConnectionStabilitySourceTests(unittest.TestCase):
    def test_final_worker_installs_stability_after_consistency_authority(self) -> None:
        source = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        consistency = source.index("install_custom_execution_consistency_authority()")
        stability = source.index("install_custom_strategy_connection_stability_fix()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(consistency, stability)
        self.assertLess(stability, bot)

    def test_live_session_watchdog_never_recycles_or_force_closes(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        watchdog = source[
            source.index("async def _stable_execution_watchdog") : source.index(
                "def _soft_private_reconnect"
            )
        ]
        self.assertNotIn("wake_private_connection", watchdog)
        self.assertNotIn("_recycle_stalled_private_session", watchdog)
        self.assertNotIn(".close(", watchdog)
        self.assertIn("if session is not None and task_alive:", watchdog)
        self.assertIn("continue", watchdog)
        self.assertIn("_schedule_targeted_runtime_repair", watchdog)

    def test_account_private_reconnect_does_not_close_socket(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        soft = source[
            source.index("def _soft_private_reconnect") : source.index(
                "def _skip_execution_driven_public_reconnect"
            )
        ]
        self.assertNotIn(".close(", soft)
        self.assertIn("wake_private_connection", soft)
        self.assertIn("forced_disconnect=false", soft)
        self.assertIn("public_reconnect=false", soft)

    def test_account_private_failures_cannot_restart_public_market_stream(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "consistency._request_public_reconnect = _skip_execution_driven_public_reconnect",
            source,
        )
        self.assertNotIn("request_reconnect(", source)
        self.assertIn("public_stream_owner=public_websocket_resilience", source)

    def test_vps_recovery_watchdog_is_rebound_but_oauth_wrapper_is_preserved(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "vps_recovery._stalled_execution_watchdog = _stable_execution_watchdog",
            source,
        )
        self.assertNotIn("RFDir5TradingBot.validate_accounts =", source)
        self.assertNotIn("RFDir5TradingBot.run =", source)


if __name__ == "__main__":
    unittest.main()
