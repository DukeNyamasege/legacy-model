from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace

from app.ai_digit_recovery_v1 import (
    AIDR_STRATEGY_CONTRACT,
    MIN_LIVE_EDGE,
    POST_VIRTUAL_BARRIER,
    RECOVERY_BARRIER,
    _read_split_remaining,
    calculate_full_recovery_stake,
    remaining_recovery_debt,
)
from app.aidr_loss_continuation_fix import (
    FIRST_RECOVERY_ROLE,
    NORMAL_ROLE,
    POST_VIRTUAL_ROLE,
    _candidate_role,
    _selected_role,
)
from app.aidr_execution_flow_fix import _required_aidr_action
from app.aidr_strict_recovery_guard import _debt_requires_virtual
from app.final_personal_trade_stream import _virtual_rows_with_progress
from app.strategy.decision_engine import parse_proposal_economics


class AIDRRecoveryV2Tests(unittest.TestCase):
    def test_runtime_uses_canonical_strategy_contract(self) -> None:
        execution = AIDR_STRATEGY_CONTRACT["execution"]
        quality = AIDR_STRATEGY_CONTRACT["quality"]
        self.assertEqual(RECOVERY_BARRIER, execution["first_recovery_barrier"])
        self.assertEqual(POST_VIRTUAL_BARRIER, execution["post_virtual_recovery_barrier"])
        self.assertEqual(MIN_LIVE_EDGE, 0.0195)
        self.assertEqual(quality["quality_tightening_factor"], 1.3)

    def test_first_and_post_virtual_recovery_use_distinct_barriers(self) -> None:
        self.assertEqual(RECOVERY_BARRIER, 3)
        self.assertEqual(POST_VIRTUAL_BARRIER, 4)
        self.assertEqual(
            _candidate_role(SimpleNamespace(barrier="3", trigger_name="", direction="")),
            FIRST_RECOVERY_ROLE,
        )
        self.assertEqual(
            _candidate_role(SimpleNamespace(barrier="4", trigger_name="", direction="")),
            POST_VIRTUAL_ROLE,
        )

    def test_account_state_allows_only_its_exact_aidr_role(self) -> None:
        self.assertEqual(
            _required_aidr_action(
                mode="NORMAL_MODE",
                split_remaining=0,
                recovery_debt=0.0,
            ),
            ("1", False, "real_over1_normal"),
        )
        self.assertEqual(
            _required_aidr_action(
                mode="RECOVERY_PENDING",
                split_remaining=0,
                recovery_debt=2.0,
            ),
            ("3", False, "real_over3_first_recovery"),
        )
        self.assertEqual(
            _required_aidr_action(
                mode="VIRTUAL_MODE",
                split_remaining=0,
                recovery_debt=5.0,
            ),
            ("4", True, "virtual_over4"),
        )
        self.assertEqual(
            _required_aidr_action(
                mode="RECOVERY_PENDING",
                split_remaining=1,
                recovery_debt=5.0,
            ),
            ("4", False, "real_over4_full_recovery"),
        )
        self.assertEqual(
            _required_aidr_action(
                mode="NORMAL_MODE",
                split_remaining=0,
                recovery_debt=0.35,
            ),
            ("3", False, "real_over3_first_recovery"),
        )

    def test_virtual_history_starts_a_new_sequence_after_each_two_wins(self) -> None:
        start = datetime(2026, 8, 3, tzinfo=timezone.utc)
        rows = [
            SimpleNamespace(
                id=index,
                virtual_trade_id=f"virtual-{index}",
                signal_id=f"signal-{index}",
                market="R_25",
                barrier="4",
                simulated_stake=2.0,
                expected_payout=3.0,
                result="VIRTUAL_WIN",
                reason="Hypothetical Outcome - No Purchase",
                actual_last_digit=8,
                exit_spot=100.0 + index,
                created_at=start + timedelta(seconds=index),
                settled_at=start + timedelta(seconds=index + 1),
            )
            for index in range(1, 5)
        ]

        payloads = _virtual_rows_with_progress(rows)

        self.assertEqual(
            [row["virtual_win_sequence"] for row in payloads],
            [1, 2, 1, 2],
        )
        self.assertEqual(
            [row["contract_type"].rsplit(" ", 1)[-1] for row in payloads],
            ["1/2", "2/2", "1/2", "2/2"],
        )

    def test_virtual_history_uses_persisted_progress_snapshot(self) -> None:
        start = datetime(2026, 8, 3, tzinfo=timezone.utc)
        rows = [
            SimpleNamespace(
                id=index,
                virtual_trade_id=f"stored-{index}",
                signal_id=f"signal-{index}",
                market="R_25",
                barrier="4",
                simulated_stake=2.0,
                expected_payout=3.0,
                result="VIRTUAL_WIN",
                reason=f"Hypothetical Outcome - No Purchase | progress={progress}/2",
                actual_last_digit=8,
                exit_spot=100.0 + index,
                created_at=start + timedelta(seconds=index),
                settled_at=start + timedelta(seconds=index + 1),
            )
            for index, progress in enumerate((1, 2, 1), start=1)
        ]

        payloads = _virtual_rows_with_progress(rows)

        self.assertEqual(
            [row["virtual_win_sequence"] for row in payloads],
            [1, 2, 1],
        )

    def test_full_recovery_stake_covers_debt_in_one_profit_target(self) -> None:
        stake = calculate_full_recovery_stake(
            base_stake=0.50,
            recovery_debt=1.00,
            proposal_profit_ratio=0.50,
        )
        self.assertEqual(stake, 2.02)
        self.assertGreaterEqual(round(stake * 0.50, 2), 1.01)

    def test_full_recovery_stake_never_drops_below_base(self) -> None:
        self.assertEqual(
            calculate_full_recovery_stake(
                base_stake=0.50,
                recovery_debt=0.01,
                proposal_profit_ratio=1.0,
            ),
            0.50,
        )

    def test_recovery_covers_debt_after_market_payout_markup(self) -> None:
        economics = parse_proposal_economics(
            {
                "proposal": {
                    "id": "market-specific-recovery",
                    "ask_price": "0.60",
                    "payout": "0.96",
                }
            },
            stake=0.60,
            predicted_probability=0.80,
            requested_monotonic=1.0,
            received_monotonic=1.1,
            app_markup_percentage=3.0,
        )
        profit_ratio = economics.potential_profit / economics.stake
        stake = calculate_full_recovery_stake(
            base_stake=0.35,
            recovery_debt=0.35,
            proposal_profit_ratio=profit_ratio,
        )

        self.assertAlmostEqual(profit_ratio, 0.552)
        self.assertEqual(stake, 0.66)
        self.assertGreaterEqual(stake * profit_ratio, 0.36)

    def test_settlement_never_erases_an_unrecovered_remainder(self) -> None:
        self.assertEqual(
            remaining_recovery_debt(
                recovery_debt=0.35,
                recovered_profit=0.33,
            ),
            0.02,
        )
        self.assertEqual(
            remaining_recovery_debt(
                recovery_debt=0.35,
                recovered_profit=0.36,
            ),
            0.0,
        )

    def test_legacy_two_target_marker_migrates_to_one_full_recovery(self) -> None:
        repo = SimpleNamespace(runtime_preference=lambda _key: "2")
        self.assertEqual(_read_split_remaining(repo, 42), 1)

    def test_role_arbitration_rotates_without_starving_normal_accounts(self) -> None:
        bot = SimpleNamespace(_aidr_last_execution_role=POST_VIRTUAL_ROLE)
        qualified = {
            POST_VIRTUAL_ROLE: [(1.0, object(), object())],
            FIRST_RECOVERY_ROLE: [(1.0, object(), object())],
            NORMAL_ROLE: [(1.0, object(), object())],
        }
        self.assertEqual(_selected_role(bot, qualified), FIRST_RECOVERY_ROLE)
        bot._aidr_last_execution_role = FIRST_RECOVERY_ROLE
        self.assertEqual(_selected_role(bot, qualified), NORMAL_ROLE)

    def test_one_loss_always_gets_first_recovery_before_virtual_mode(self) -> None:
        self.assertFalse(
            _debt_requires_virtual(
                debt=10.0,
                consecutive_losses=1,
                split_remaining=0,
            )
        )
        self.assertTrue(
            _debt_requires_virtual(
                debt=10.0,
                consecutive_losses=2,
                split_remaining=0,
            )
        )


if __name__ == "__main__":
    unittest.main()
