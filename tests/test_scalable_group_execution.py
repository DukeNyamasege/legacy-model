from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import app.deriv_request_broker as request_broker
import app.scalable_group_execution as grouped
from app.deriv_bulk_rest_credentials import credential_status_from_execution


ROOT = Path(__file__).resolve().parents[1]


class RestBulkExecutionHelperTests(unittest.IsolatedAsyncioTestCase):
    def test_transient_bulk_errors_are_outcome_unknown_not_replayed(self) -> None:
        self.assertTrue(
            grouped._outcome_unknown(
                {
                    "error": {
                        "code": "HTTP_502",
                        "message": "An upstream dependency was unavailable.",
                    }
                }
            )
        )
        self.assertTrue(
            grouped._outcome_unknown(
                {
                    "error": {
                        "code": "REQUEST_TIMEOUT",
                        "message": "Bulk purchase request timed out",
                    }
                }
            )
        )
        self.assertFalse(
            grouped._outcome_unknown(
                {
                    "error": {
                        "code": "PAT_REQUIRED",
                        "message": "Link your Deriv API token with trade scope.",
                    }
                }
            )
        )

    async def test_missing_api_token_is_rejected_before_rest_bulk_call(self) -> None:
        status_updates: list[tuple[int | None, str, str]] = []
        bot = SimpleNamespace(
            _managed_account_id_for_token=lambda token: 7,
            rf_repository=SimpleNamespace(
                virtual_protection_for_account=lambda **kwargs: {"mode": "NORMAL_MODE"}
            ),
            _account_environment_for_token=lambda token: "demo",
            _real_trading_allowed=lambda: False,
            _bulk_purchase_token_capable=lambda token: False,
            _set_account_execution_status=lambda managed_id, status, reason="": status_updates.append(
                (managed_id, status, reason)
            ),
            logger=SimpleNamespace(
                warning=lambda *args, **kwargs: None,
                exception=lambda *args, **kwargs: None,
            ),
        )
        signal = SimpleNamespace(
            signal_id="sig-1",
            symbol="1HZ100V",
            contract_type="DIGITOVER",
            barrier="1",
        )

        result = await grouped._grouped_purchase_accounts_by_stake(
            bot,
            signal=signal,
            eligible_accounts=[("runtime-token", "DOT123456")],
            stake_by_token={"runtime-token": 0.50},
            pre_trade_profit_ratio=0.90,
        )

        self.assertEqual(result[0]["execution_transport"], "REST_BULK_PURCHASE")
        self.assertEqual(result[0]["error"]["code"], "PAT_REQUIRED")
        self.assertIn("trade scope", result[0]["error"]["message"])
        self.assertEqual(status_updates[0][1], "bulk_execution_pat_required")


class CredentialStatusTests(unittest.TestCase):
    def test_connected_api_token_hides_credential_input(self) -> None:
        status = credential_status_from_execution("active", has_token=True)
        self.assertTrue(status["connected"])
        self.assertEqual(status["status"], "connected")
        self.assertEqual(status["label"], "Connected")

    def test_expired_token_message_points_to_settings_credentials(self) -> None:
        status = credential_status_from_execution(
            "credential_error",
            "Deriv API token expired or was rejected",
            has_token=False,
        )
        self.assertFalse(status["connected"])
        self.assertEqual(status["status"], "expired")
        self.assertIn("Settings > Credentials", status["message"])

    def test_missing_token_prompts_trade_scope_api_token(self) -> None:
        status = credential_status_from_execution("bulk_execution_pat_required")
        self.assertFalse(status["connected"])
        self.assertEqual(status["status"], "missing")
        self.assertIn("Deriv API token", status["message"])
        self.assertIn("Security & limits", status["message"])
        self.assertIn("trade scope", status["message"])
        self.assertNotIn("Personal Access Token", status["message"])


class RequestBrokerHelperTests(unittest.TestCase):
    def test_official_bulk_purchase_path_is_allowed(self) -> None:
        self.assertTrue(
            request_broker._request_is_bulk_purchase(
                "/trading/v1/options/contracts/bulk-purchase/demo"
            )
        )
        self.assertFalse(
            request_broker._request_is_bulk_purchase(
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
        bulk_key = request_broker._coalesce_key(
            "POST",
            "/trading/v1/options/contracts/bulk-purchase/demo",
            "",
            {"accounts": []},
        )
        self.assertIsNotNone(account_key)
        self.assertIsNone(otp_key)
        self.assertIsNone(bulk_key)

    def test_financial_posts_are_single_attempt_to_avoid_duplicate_contracts(self) -> None:
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
        self.assertEqual(
            broker._attempts(
                "POST",
                "/trading/v1/options/contracts/bulk-purchase/demo",
            ),
            1,
        )


class DeploymentSourceInvariantTests(unittest.TestCase):
    def test_worker_installs_final_execution_authorities_in_order(self) -> None:
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

    def test_financial_execution_is_rest_bulk_purchase(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("REST_BULK_PURCHASE_PLAN", source)
        self.assertIn("transport=REST_BULK_PURCHASE", source)
        self.assertIn("private_websocket_buy=false", source)
        self.assertIn("bulk_purchase=true", source)
        self.assertIn("RFDir5TradingBot._purchase_accounts_by_stake", source)
        self.assertIn("_BASE_REST_BULK_PURCHASE", source)
        self.assertIn("_bulk_purchase_token_capable", source)
        self.assertNotIn("PRIVATE_WS_EXECUTION_PLAN", source)
        self.assertNotIn("_purchase_via_private_sessions", source)

    def test_provider_confirmation_diagnostics_preserve_exact_rest_result(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_transport_outcomes_by_signal", source)
        self.assertIn("provider_confirmed_registration_missing", source)
        self.assertIn("provider_confirmation_unknown", source)
        self.assertIn("standardized._missing_reason = exact_missing_reason", source)
        self.assertIn("_ACTIVE_RECEIPT_SIGNAL_ID", source)
        self.assertIn("REST_BULK_PURCHASE", source)

    def test_request_broker_allows_bulk_purchase_and_keeps_setup_bounded(self) -> None:
        source = (ROOT / "app" / "deriv_request_broker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("aiohttp.TCPConnector", source)
        self.assertIn("limit_per_host", source)
        self.assertIn("keepalive_timeout=30", source)
        self.assertIn("_coalesce_key", source)
        self.assertIn("DERIV_ACCOUNT_LIST_CACHE_SECONDS", source)
        self.assertIn("financial_execution_transport=REST_BULK_PURCHASE", source)
        self.assertIn("official_bulk_purchase_enabled=true", source)
        self.assertNotIn("MULTI_ACCOUNT_REST_TRADE_BLOCKED", source)
        self.assertNotIn("MULTI_ACCOUNT_REST_TRADE_DISABLED", source)
        self.assertIn("enhanced_bot._rest_request = _brokered_rest_request", source)
        self.assertIn("await _BROKER.close()", source)

    def test_system_roles_use_task_local_scopes(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ContextVar", source)
        self.assertIn("_AIDR_SCOPE_IDS", source)
        self.assertIn("_AIDR_RECOVERY_ENABLED", source)
        self.assertIn("task_local_role_scopes=true", source)
        self.assertIn("global_stop_on_account_error=false", source)

    def test_account_errors_cannot_stop_global_execution(self) -> None:
        grouped_source = (
            ROOT / "app" / "scalable_group_execution.py"
        ).read_text(encoding="utf-8")
        self.assertIn("global_execution_continues=true", grouped_source)
        self.assertIn("global_stop_on_account_error=false", grouped_source)
        self.assertNotIn("repository.set_status(\"STOPPED\")", grouped_source)
        self.assertNotIn("bot.is_running = False", grouped_source)
        self.assertNotIn("self.is_running = False", grouped_source)


if __name__ == "__main__":
    unittest.main()
