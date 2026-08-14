from __future__ import annotations

import unittest
from pathlib import Path

from app.custom_execution_consistency_authority import _exact_split_stake
from app.custom_split_equal_spread_authority import equal_split_recovery_stake


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "app" / "custom_execution_consistency_authority.py"
EQUAL_SPLIT_AUTHORITY = ROOT / "app" / "custom_split_equal_spread_authority.py"
WORKER = ROOT / "app" / "custom_strategy_worker.py"


class CustomExecutionConsistencyAuthorityTests(unittest.TestCase):
    def test_final_authorities_install_after_connection_and_stop_layers(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        stop_reason = source.index("install_execution_stop_reason_authority()")
        connection = source.index("install_custom_strategy_connection_stampede_guard()")
        consistency = source.index("install_custom_execution_consistency_authority()")
        equal_split = source.index("install_custom_split_equal_spread_authority()")
        self.assertLess(stop_reason, connection)
        self.assertLess(connection, consistency)
        self.assertLess(consistency, equal_split)
        self.assertIn("runtime_fault_policy=reconnect_reconcile_never_stop", source)
        self.assertIn("ambiguous_buy_policy=reconcile_before_next_real", source)
        self.assertIn("duplicate_buy_retry=false", source)
        self.assertIn("martingale_split=equal_loss_pool_by_configured_split_count", source)
        self.assertIn("split_rebase_after_actual_loss=true", source)

    def test_timeout_is_reconnect_and_reconciliation_not_terminal_error(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("class AmbiguousPurchaseTimeout", source)
        self.assertIn("_request_private_reconnect", source)
        self.assertIn("Purchase acknowledgement was not received", source)
        self.assertIn("duplicate_buy_retry=false", source)
        self.assertIn("_custom_purchase_reconciliation_pending", source)
        self.assertIn("_reconcile_ambiguous_purchase", source)
        self.assertIn("profit_table", source)
        self.assertIn("portfolio", source)
        self.assertIn("reconnect_instead_of_execution_stop", source)
        self.assertIn("enabled_preserved=true", source)
        self.assertNotIn('self.bot._set_account_execution_status(\n                self.managed_account_id,\n                "error"', source)

    def test_private_request_deadline_is_not_a_lifecycle_stop(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("_PRIVATE_FINANCIAL_REQUEST_SECONDS = 15.0", source)
        self.assertIn(
            "bridge._private_request_timeout = lambda: _PRIVATE_FINANCIAL_REQUEST_SECONDS",
            source,
        )
        self.assertIn("timeout_policy=reconcile_never_stop", source)

    def test_virtual_hook_is_persisted_and_pushed_immediately(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn('"virtual_protection"', source)
        self.assertIn("CUSTOM_VIRTUAL_HOOK_ENTERED", source)
        self.assertIn("CUSTOM_VIRTUAL_HOOK_VISIBLE", source)
        self.assertIn("status_persisted_immediately=true", source)
        self.assertIn("dashboard_wakeup=true", source)
        self.assertIn("_dashboard_wakeup", source)

    def test_multiplier_is_previous_actual_stake_times_multiplier_only(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("previous_stake = _latest_actual_stake(self, managed_id) or base", source)
        self.assertIn("target = ceil_cents(previous_stake * multiplier)", source)
        self.assertIn("CUSTOM_PURE_MULTIPLIER_STAKE", source)
        self.assertIn("debt_sizing=false payout_sizing=false", source)
        self.assertIn("previous_actual_stake_times_multiplier", WORKER.read_text(encoding="utf-8"))

    def test_lower_split_math_has_no_hidden_buffer(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertNotIn("debt * 0.06", source)
        self.assertNotIn("max(0.05,", source)
        self.assertIn("target_profit = debt / parts", source)
        self.assertIn("part_stake = ceil_cents(max(base, target_profit / ratio))", source)

        one, full_one = _exact_split_stake(
            base_stake=0.35,
            recovery_debt=10.0,
            proposal_profit_ratio=0.50,
            remaining_parts=1,
        )
        two, full_two = _exact_split_stake(
            base_stake=0.35,
            recovery_debt=10.0,
            proposal_profit_ratio=0.50,
            remaining_parts=2,
        )
        three, full_three = _exact_split_stake(
            base_stake=0.35,
            recovery_debt=10.0,
            proposal_profit_ratio=0.50,
            remaining_parts=3,
        )
        self.assertEqual(one, 20.0)
        self.assertEqual(two, 10.0)
        self.assertEqual(three, 6.67)
        self.assertEqual(full_one, 20.0)
        self.assertEqual(full_two, 20.0)
        self.assertEqual(full_three, 20.0)

    def test_final_split_always_divides_by_configured_spread_not_remaining_legs(self) -> None:
        source = EQUAL_SPLIT_AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("divisor=configured_split_count", source)
        self.assertIn("target_profit_per_leg = debt / parts", source)
        self.assertIn("split_count=split_count", source)
        self.assertNotIn("remaining_parts", source)

        first, full, target = equal_split_recovery_stake(
            base_stake=20.0,
            recovery_basis_debt=60.0,
            proposal_profit_ratio=0.55,
            split_count=2,
        )
        second, full_again, target_again = equal_split_recovery_stake(
            base_stake=20.0,
            recovery_basis_debt=60.0,
            proposal_profit_ratio=0.55,
            split_count=2,
        )
        self.assertEqual(full, 109.10)
        self.assertEqual(first, 54.55)
        self.assertEqual(second, 54.55)
        self.assertEqual(full_again, full)
        self.assertEqual(target, 30.0)
        self.assertEqual(target_again, target)

    def test_split_loss_rebases_full_configured_spread(self) -> None:
        source = EQUAL_SPLIT_AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("CUSTOM_EQUAL_SPLIT_REBASED_AFTER_LOSS", source)
        self.assertIn("manual._write_split_remaining(self, managed_id, split_count)", source)
        self.assertIn('"manual_split_remaining": split_count', source)
        self.assertIn('"manual_split_rebased_after_loss": bool(was_recovery)', source)
        self.assertIn("actual_loss_rebases_full_configured_spread", source)

    def test_configured_split_count_cannot_create_hidden_cleanup_trade(self) -> None:
        source = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("_finish_split_without_hidden_cleanup", source)
        self.assertIn('"manual_split_remaining": 0', source)
        self.assertIn('"manual_split_residual_unrecovered": residual', source)
        self.assertIn("extra_cleanup_trade=false", source)
        self.assertIn("session_profit_preserved=true", source)


if __name__ == "__main__":
    unittest.main()
