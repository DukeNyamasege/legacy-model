from __future__ import annotations

import unittest
from pathlib import Path

import app.scalable_group_execution as grouped


ROOT = Path(__file__).resolve().parents[1]


class GroupingHelperTests(unittest.TestCase):
    def test_chunks_respect_provider_limit(self) -> None:
        values = list(range(250))
        chunks = grouped._chunks(values, 100)
        self.assertEqual([len(chunk) for chunk in chunks], [100, 100, 50])
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))

    def test_only_explicit_rate_limit_is_safe_for_bulk_replay(self) -> None:
        self.assertTrue(
            grouped._safe_bulk_retry(
                {
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests",
                    }
                }
            )
        )
        self.assertFalse(
            grouped._safe_bulk_retry(
                {
                    "error": {
                        "code": "CONNECTION_ERROR",
                        "message": "Connection timed out",
                    }
                }
            )
        )
        self.assertFalse(
            grouped._safe_bulk_retry(
                {
                    "error": {
                        "code": "BULK_MEMBER_MISSING",
                        "message": "No transaction returned",
                    }
                }
            )
        )

    def test_unknown_purchase_outcome_is_not_treated_as_safe_retry(self) -> None:
        item = {
            "error": {
                "code": "BULK_OUTCOME_UNKNOWN",
                "message": "Bulk response timed out",
            }
        }
        self.assertTrue(grouped._outcome_unknown(item))
        self.assertFalse(grouped._safe_bulk_retry(item))


class DeploymentSourceInvariantTests(unittest.TestCase):
    def test_worker_installs_scalable_runtime_after_immediate_delivery(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        immediate = source.index("install_guaranteed_signal_delivery()")
        grouped_install = source.index("install_scalable_group_execution()")
        production = source.index("install_production_worker_integration()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(immediate, grouped_install)
        self.assertLess(grouped_install, production)
        self.assertLess(production, bot)

    def test_contract_metadata_is_public_and_not_multiplied_by_accounts(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("RF_ACCOUNT_USES_PUBLIC_CONTRACT_CACHE", source)
        self.assertIn("authenticated_contract_requests=0", source)
        self.assertIn(
            "RFDir5TradingBot._validate_account_contracts = "
            "_public_only_account_contract_validation",
            source,
        )
        self.assertNotIn(
            'session.send_request({"contracts_for": symbol})',
            source,
        )

    def test_identical_pat_contracts_use_official_bulk_shards(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("BULK_SHARD_SIZE = min(", source)
        self.assertIn("100,", source)
        self.assertIn("_purchase_stake_group_for_environment", source)
        self.assertIn("BULK_REST", source)
        self.assertIn("bulk_shard_limit=%s", source)
        self.assertIn("DERIV_BULK_CONCURRENCY", source)

    def test_oauth_accounts_remain_bounded_private_groups(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("DERIV_PRIVATE_GROUP_SIZE", source)
        self.assertIn("DERIV_PRIVATE_GROUP_CONCURRENCY", source)
        self.assertIn("_purchase_via_private_sessions", source)
        self.assertIn("GROUP_PRIVATE_CONNECTION_RETRY", source)
        self.assertIn("PRIVATE_WS", source)

    def test_unknown_buy_outcomes_are_not_blindly_replayed(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("automatic_replay=false", source)
        self.assertIn("duplicate_protection=true", source)
        self.assertIn("BULK_OUTCOME_UNKNOWN", source)
        self.assertIn("PRIVATE_BUY_OUTCOME_UNKNOWN", source)
        self.assertIn("prevent a duplicate contract", source)

    def test_provider_confirmation_diagnostics_preserve_exact_transport_result(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_transport_outcomes_by_signal", source)
        self.assertIn("provider_confirmed_registration_missing", source)
        self.assertIn("provider_outcome_unknown", source)
        self.assertIn("standardized._missing_reason = exact_missing_reason", source)
        self.assertIn("_ACTIVE_RECEIPT_SIGNAL_ID", source)

    def test_system_roles_use_task_local_scopes_and_report_every_result(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ContextVar", source)
        self.assertIn("_AIDR_SCOPE_IDS", source)
        self.assertIn("_AIDR_RECOVERY_ENABLED", source)
        self.assertIn("await asyncio.gather(*dispatch_tasks", source)
        self.assertIn("AIDR_ROLE_DISPATCH_RESULT", source)
        self.assertIn("AIDR_GROUPED_CYCLE_COMPLETE", source)
        self.assertIn("role_scope_context=task_local", source)
        self.assertIn("global_stop_on_role_error=false", source)

    def test_account_errors_cannot_stop_global_execution(self) -> None:
        source = (ROOT / "app" / "scalable_group_execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("global_execution_continues=true", source)
        self.assertIn("global_stop_on_error=false", source)
        self.assertNotIn("repository.set_status(\"STOPPED\")", source)
        self.assertNotIn("bot.is_running = False", source)
        self.assertNotIn("self.is_running = False", source)


if __name__ == "__main__":
    unittest.main()
