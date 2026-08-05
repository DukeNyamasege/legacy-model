from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import app.deriv_request_broker as request_broker
import app.scalable_group_execution as grouped


ROOT = Path(__file__).resolve().parents[1]


class WebSocketGroupingHelperTests(unittest.TestCase):
    def test_chunks_bound_logical_websocket_groups(self) -> None:
        values = list(range(53))
        chunks = grouped._chunks(values, 20)
        self.assertEqual([len(chunk) for chunk in chunks], [20, 20, 13])
        self.assertTrue(all(len(chunk) <= 20 for chunk in chunks))

    def test_only_pre_send_not_connected_failure_is_safe_to_retry(self) -> None:
        self.assertTrue(
            grouped._safe_connection_retry(
                {
                    "error": {
                        "code": "NOT_CONNECTED",
                        "message": "Private WebSocket is not connected",
                    }
                }
            )
        )
        self.assertFalse(
            grouped._safe_connection_retry(
                {
                    "error": {
                        "code": "PRIVATE_BUY_OUTCOME_UNKNOWN",
                        "message": "Private WebSocket buy confirmation timed out",
                    }
                }
            )
        )
        self.assertFalse(
            grouped._safe_connection_retry(
                {
                    "error": {
                        "code": "PRIVATE_BUY_FAILED",
                        "message": "Connection closed after send",
                    }
                }
            )
        )

    def test_unknown_confirmation_is_never_classified_as_safe_retry(self) -> None:
        item = {
            "error": {
                "code": "PRIVATE_BUY_OUTCOME_UNKNOWN",
                "message": "Private WebSocket buy confirmation timed out",
            }
        }
        self.assertTrue(grouped._outcome_unknown(item))
        self.assertFalse(grouped._safe_connection_retry(item))

    def test_recent_matching_contract_can_recover_lost_confirmation(self) -> None:
        signal = SimpleNamespace(
            symbol="1HZ100V",
            contract_type="DIGITOVER",
        )
        row = {
            "contract_id": 123,
            "underlying": "1HZ100V",
            "contract_type": "DIGITOVER",
            "buy_price": 0.50,
            "purchase_time": 1_000,
        }
        self.assertTrue(
            grouped._row_matches_unknown_buy(
                row,
                signal=signal,
                stake=0.50,
                sent_epoch=999,
            )
        )
        self.assertFalse(
            grouped._row_matches_unknown_buy(
                {**row, "buy_price": 2.00},
                signal=signal,
                stake=0.50,
                sent_epoch=999,
            )
        )


class RequestBrokerHelperTests(unittest.TestCase):
    def test_multi_account_rest_trade_path_is_blocked(self) -> None:
        self.assertTrue(
            request_broker._is_disallowed_multi_account_trade(
                "/trading/v1/options/contracts/bulk-purchase/demo"
            )
        )
        self.assertFalse(
            request_broker._is_disallowed_multi_account_trade(
                "/trading/v1/options/accounts/DOT123/otp"
            )
        )

    def test_only_account_list_reads_are_coalesced(self) -> None:
        account_key = request_broker._coalesce_key(
            "GET",
            "/trading/v1/options/accounts",
            "credential",
            None,
        )
        otp_key = request_broker._coalesce_key(
            "POST",
            "/trading/v1/options/accounts/DOT123/otp",
            "credential",
            None,
        )
        self.assertIsNotNone(account_key)
        self.assertIsNone(otp_key)

    def test_safe_setup_requests_have_bounded_retries(self) -> None:
        broker = request_broker._DerivRequestBroker()
        self.assertGreater(
            broker._attempts("GET", "/trading/v1/options/accounts"),
            1,
        )
        self.assertEqual(
            broker._attempts(
                "POST",
                "/trading/v1/options/accounts/DOT123/otp",
            ),
            2,
        )


class DeploymentSourceInvariantTests(unittest.TestCase):
    def test_worker_installs_final_websocket_authorities_in_order(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        broker = source.index("install_deriv_request_broker()")
        websocket_guard = source.index("install_websocket_only_execution()")
        immediate = source.index("install_guaranteed_signal_delivery()")
        grouped_install = source.index("install_scalable_group_execution()")
        role_hardening = source.index(
            "install_scalable_group_execution_hardening()"
        )
        production = source.index("install_production_worker_integration()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(broker, websocket_guard)
        self.assertLess(websocket_guard, immediate)
        self.assertLess(immediate, grouped_install)
        self.assertLess(grouped_install, role_hardening)
        self.assertLess(role_hardening, production)
        self.assertLess(production, bot)

    def test_contract_metadata_is_public_and_not_multiplied_by_accounts(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("RF_ACCOUNT_USES_PUBLIC_CONTRACT_CACHE", source)
        self.assertIn("authenticated_contract_metadata_requests=0", source)
        self.assertIn(
            "RFDir5TradingBot._validate_account_contracts = "
            "_public_only_account_contract_validation",
            source,
        )
        self.assertNotIn(
            'session.send_request({"contracts_for": symbol})',
            source,
        )

    def test_financial_execution_is_private_websocket_only(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("PRIVATE_WS_EXECUTION_PLAN", source)
        self.assertIn("transport=PRIVATE_WEBSOCKET_ONLY", source)
        self.assertIn("one_authenticated_socket_per_account=true", source)
        self.assertIn("RFDir5TradingBot._purchase_accounts_by_stake", source)
        self.assertIn("_purchase_via_private_sessions", source)
        self.assertNotIn("_purchase_stake_group_for_environment", source)
        self.assertNotIn("BULK_REST", source)
        self.assertNotIn("_bulk_purchase_token_capable", source)

    def test_websocket_fanout_is_bounded_and_per_account_serialized(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("DERIV_WS_GROUP_SIZE", source)
        self.assertIn("DERIV_WS_GROUP_CONCURRENCY", source)
        self.assertIn("DERIV_WS_GROUP_START_INTERVAL_SECONDS", source)
        self.assertIn("asyncio.Semaphore", source)
        self.assertIn("_account_buy_lock", source)
        self.assertIn("_wait_group_start_slot", source)
        self.assertIn("PRIVATE_WS_GROUP_DISPATCH", source)
        self.assertIn("PRIVATE_WS_GROUP_RESULT", source)

    def test_unknown_buy_outcomes_are_reconciled_not_replayed(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("PRIVATE_BUY_OUTCOME_UNKNOWN", source)
        self.assertIn("_recover_unknown_confirmation", source)
        self.assertIn('"portfolio": 1', source)
        self.assertIn('"profit_table": 1', source)
        self.assertIn("buy_replayed=false", source)
        self.assertIn("will not be replayed automatically", source)
        self.assertIn("prevent a duplicate contract", source)

    def test_provider_echo_has_safe_request_correlation(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("correlated_direct_buy", source)
        self.assertIn('request["passthrough"]', source)
        self.assertIn('"signal_id"', source)
        self.assertIn('"managed_account_id"', source)
        self.assertIn('"websocket_group_id"', source)
        self.assertNotIn('"token"', source)

    def test_request_broker_is_account_setup_only(self) -> None:
        source = (ROOT / "app" / "deriv_request_broker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("aiohttp.TCPConnector", source)
        self.assertIn("limit_per_host", source)
        self.assertIn("keepalive_timeout=30", source)
        self.assertIn("_coalesce_key", source)
        self.assertIn("DERIV_ACCOUNT_LIST_CACHE_SECONDS", source)
        self.assertIn("MULTI_ACCOUNT_REST_TRADE_BLOCKED", source)
        self.assertIn("MULTI_ACCOUNT_REST_TRADE_DISABLED", source)
        self.assertIn("PRIVATE_WEBSOCKET_ONLY", source)
        self.assertIn("enhanced_bot._rest_request = _brokered_rest_request", source)
        self.assertIn("await _BROKER.close()", source)

    def test_provider_confirmation_diagnostics_preserve_exact_ws_result(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_transport_outcomes_by_signal", source)
        self.assertIn("provider_confirmed_registration_missing", source)
        self.assertIn("provider_confirmation_unknown", source)
        self.assertIn("standardized._missing_reason = exact_missing_reason", source)
        self.assertIn("_ACTIVE_RECEIPT_SIGNAL_ID", source)

    def test_system_roles_use_task_local_scopes(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ContextVar", source)
        self.assertIn("_AIDR_SCOPE_IDS", source)
        self.assertIn("_AIDR_RECOVERY_ENABLED", source)
        self.assertIn("task_local_role_scopes=true", source)
        self.assertIn("global_stop_on_account_error=false", source)

    def test_each_system_role_uses_a_fresh_websocket_subcycle(self) -> None:
        source = (
            ROOT / "app" / "scalable_group_execution_hardening.py"
        ).read_text(encoding="utf-8")
        self.assertIn("for role in standardized.AIDR_EXECUTION_ORDER", source)
        self.assertIn("AIDR_ROLE_SUBCYCLE_STARTED", source)
        self.assertIn("fresh_proposal=true", source)
        self.assertIn("AIDR_ROLE_SUBCYCLE_COMPLETE", source)
        self.assertIn("signal_created_for_this_subcycle=true", source)
        self.assertIn("AIDR_ROLE_DISPATCH_RESULT", source)
        self.assertIn("private_websocket_only=true", source)
        self.assertNotIn("_bulk_purchase_token_capable", source)

    def test_compose_exposes_only_websocket_group_scaling(self) -> None:
        source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("DERIV_WS_GROUP_SIZE", source)
        self.assertIn("DERIV_WS_GROUP_CONCURRENCY", source)
        self.assertIn("DERIV_WS_ACCOUNT_BUY_TIMEOUT_SECONDS", source)
        self.assertNotIn("DERIV_BULK_SHARD_SIZE", source)
        self.assertNotIn("DERIV_BULK_CONCURRENCY", source)

    def test_account_errors_cannot_stop_global_execution(self) -> None:
        grouped_source = (
            ROOT / "app" / "scalable_group_execution.py"
        ).read_text(encoding="utf-8")
        role_source = (
            ROOT / "app" / "scalable_group_execution_hardening.py"
        ).read_text(encoding="utf-8")
        source = grouped_source + "\n" + role_source
        self.assertIn("global_execution_continues=true", source)
        self.assertIn("global_stop_on_error=false", source)
        self.assertNotIn("repository.set_status(\"STOPPED\")", source)
        self.assertNotIn("bot.is_running = False", source)
        self.assertNotIn("self.is_running = False", source)


if __name__ == "__main__":
    unittest.main()
