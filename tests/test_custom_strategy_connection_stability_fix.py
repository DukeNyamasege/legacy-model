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

    def test_handshake_capacity_is_reserved_before_one_time_otp_is_requested(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        connect = source[
            source.index("async def _fresh_otp_connect_and_run") : source.index(
                "def install_custom_strategy_connection_stability_fix"
            )
        ]
        slot = connect.index("async with gate._handshake_slots")
        schedule = connect.index("await gate.wait_for_start_slot()")
        otp = connect.index("url = await self.get_otp_url()")
        websocket = connect.index("websocket = await websockets.connect(")
        self.assertLess(slot, schedule)
        self.assertLess(schedule, otp)
        self.assertLess(otp, websocket)
        self.assertNotIn("gate.open_websocket(url)", connect)

    def test_final_installer_rebinds_client_session_before_bot_creation(self) -> None:
        stability_source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        worker_source = (ROOT / "app" / "custom_strategy_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "ClientSession.connect_and_run = _fresh_otp_connect_and_run",
            stability_source,
        )
        self.assertLess(
            worker_source.index("install_custom_strategy_connection_stability_fix()"),
            worker_source.index("bot = RFDir5TradingBot()"),
        )

    def test_multiplier_affordability_is_a_financial_skip_not_transport_fault(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"multiplier stake"', source)
        self.assertIn('"exceeds spendable balance"', source)
        self.assertIn(
            "consistency._stake_policy_reason = _financial_stake_policy_reason",
            source,
        )
        self.assertIn(
            "continuity._is_stake_policy_reason = _financial_stake_policy_reason",
            source,
        )
        self.assertIn(
            "martingale_authority._is_stake_policy_rejection = _financial_stake_policy_reason",
            source,
        )
        self.assertIn("_stake_policy_transport_isolation = True", source)


if __name__ == "__main__":
    unittest.main()
