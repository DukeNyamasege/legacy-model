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

    def test_account_private_reconnect_priority_wakes_one_idle_account_only(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        soft = source[
            source.index("def _soft_private_reconnect") : source.index(
                "def _skip_execution_driven_public_reconnect"
            )
        ]
        self.assertNotIn(".close(", soft)
        self.assertIn("PRIVATE_WS_PRIORITY_WAKE", soft)
        self.assertIn("wake_scope=single_account", soft)
        self.assertIn("_priority_wake_due", soft)
        self.assertIn("_private_otp_inflight", soft)
        self.assertIn("_private_ws_handshake_inflight", soft)
        self.assertIn("forced_disconnect=false", soft)
        self.assertIn("public_reconnect=false", soft)
        self.assertIn("_schedule_targeted_runtime_repair", soft)
        self.assertIn("_notice_due", soft)
        self.assertIn("ui_reason", soft)

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
        self.assertIn("_PUBLIC_RECONNECT_SKIP_LOG_INTERVAL_SECONDS", source)

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

    def test_deriv_otp_rest_no_longer_holds_websocket_handshake_slot(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        connect = source[
            source.index("async def _fresh_otp_connect_and_run") : source.index(
                "def install_custom_strategy_connection_stability_fix"
            )
        ]
        bootstrap = connect.index("async with bootstrap_slots")
        schedule = connect.index("await gate.wait_for_start_slot()")
        wait_for = connect.index("url = await asyncio.wait_for(")
        otp = connect.index("_fresh_otp_url(self)")
        ws_slot = connect.index("async with gate._handshake_slots")
        websocket = connect.index("websocket = await websockets.connect(")

        self.assertLess(bootstrap, schedule)
        self.assertLess(schedule, wait_for)
        self.assertLess(wait_for, otp)
        self.assertLess(otp, ws_slot)
        self.assertLess(ws_slot, websocket)
        self.assertIn("handshake_slot_held_during_otp=false", source)
        self.assertIn("handshake_slot_held=false", connect)
        self.assertIn("PRIVATE_WS_OTP_READY", connect)
        self.assertIn("otp_validity_seconds=120", connect)
        self.assertIn("timeout=_OTP_BOOTSTRAP_TIMEOUT_SECONDS", connect)
        self.assertIn("PRIVATE_WS_OTP_TIMEOUT", connect)
        self.assertIn("_private_otp_inflight", connect)
        self.assertIn("_private_ws_handshake_inflight", connect)
        self.assertNotIn("gate.open_websocket(url)", connect)

    def test_otp_rest_uses_shared_keepalive_auto_network_pool(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        otp_pool = source[
            source.index("def _otp_http_session") : source.index(
                "def _otp_error_from_payload"
            )
        ]
        otp_fetch = source[
            source.index("async def _fresh_otp_url") : source.index(
                "async def _stable_execution_watchdog"
            )
        ]
        self.assertIn("aiohttp.TCPConnector", otp_pool)
        self.assertNotIn("family=socket.AF_INET", otp_pool)
        self.assertIn("family=auto", otp_pool)
        self.assertIn("keepalive_timeout=20", otp_pool)
        self.assertIn("ttl_dns_cache=120", otp_pool)
        self.assertIn("aiohttp.ClientSession", otp_pool)
        self.assertIn("PRIVATE_WS_OTP_HTTP_POOL_ACTIVE", otp_pool)
        self.assertIn("session.post(endpoint, headers=headers)", otp_fetch)
        self.assertIn("deriv_headers", otp_fetch)

    def test_private_websocket_open_uses_returned_url_directly(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        connect = source[
            source.index("async def _fresh_otp_connect_and_run") : source.index(
                "def install_custom_strategy_connection_stability_fix"
            )
        ]
        self.assertIn("open_timeout=_PRIVATE_WS_OPEN_TIMEOUT_SECONDS", connect)
        self.assertNotIn("family=socket.AF_INET", connect)
        self.assertIn("network_family=auto", connect)
        self.assertIn("ping_interval=20", connect)
        self.assertIn("ping_timeout=20", connect)

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
        self.assertIn("_private_ws_execution_wake_enabled = True", stability_source)
        self.assertIn("_private_ws_priority_wake_interval_seconds", stability_source)
        self.assertIn("_private_ws_otp_bootstrap_concurrency", stability_source)
        self.assertIn("_private_ws_otp_bootstrap_timeout_seconds", stability_source)
        self.assertIn("_private_ws_otp_retry_max_seconds", stability_source)
        self.assertIn("_private_ws_otp_http_keepalive = True", stability_source)
        self.assertIn("_private_ws_network_family = \"auto\"", stability_source)
        self.assertIn("_private_ws_ipv4_transport = False", stability_source)
        self.assertIn("_private_ws_open_timeout_seconds", stability_source)
        self.assertIn("_private_ws_exact_error_ui = True", stability_source)
        self.assertLess(
            worker_source.index("install_custom_strategy_connection_stability_fix()"),
            worker_source.index("bot = RFDir5TradingBot()"),
        )

    def test_ui_receives_exact_private_transport_errors(self) -> None:
        source = (
            ROOT / "app" / "custom_strategy_connection_stability_fix.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def _set_execution_transport_status", source)
        self.assertIn("Scanner may be ready", source)
        self.assertIn("BUY is blocked", source)
        self.assertIn("ui_reason", source)
        self.assertIn("Private Deriv execution WebSocket failed:", source)
        self.assertIn("Deriv OTP REST request timed out", source)

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
