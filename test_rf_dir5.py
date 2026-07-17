from __future__ import annotations

import time
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import func, select

from app.config import load_test2_config
from app.database import Database
from app.model.bayesian_probability import BayesianGroupKey, KeyedBayesianProbability
from app.models import ManagedAccount, ShadowContract, Trade
from app.repositories.rf_dir5_repository import RFDir5Repository
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy.decision_engine import (
    ProposalEconomics,
    RiseFallDecisionEngine,
    parse_proposal_economics,
)
from app.strategy.rise_fall_strategy import (
    RF_DIR5_VERSION,
    build_five_move_features,
    detect_fall_candidate,
    detect_rise_candidate,
    make_signal_event,
    shadow_outcome,
)


def features(prices: list[str]):
    return build_five_move_features(
        prices,
        normalization_movements=[Decimal("0.10")] * 100,
    )


def signal(direction: str = "RISE", tick_sequence: int = 200):
    values = (
        ["100.00", "100.10", "100.20", "100.30", "100.25", "100.40"]
        if direction == "RISE"
        else ["100.40", "100.30", "100.20", "100.10", "100.15", "100.00"]
    )
    item = features(values)
    return make_signal_event(
        run_id="rf-test",
        symbol="1HZ100V",
        direction=direction,
        duration_ticks=5,
        features=item,
        quality_score=7,
        signal_tick_epoch=1_700_000_000,
        signal_tick_id=f"tick-{tick_sequence}",
        connection_session_id="connection-1",
        tick_sequence=tick_sequence,
    )


class RiseFallFeatureTests(unittest.TestCase):
    def test_six_quotes_create_exactly_five_movements(self) -> None:
        item = features(["1.00", "1.10", "1.20", "1.30", "1.40", "1.50"])
        self.assertEqual(len(item.analysis_quotes), 6)
        self.assertEqual(len(item.movements), 5)

    def test_rise_and_fall_rules_are_symmetric(self) -> None:
        rise = features(["1.00", "1.10", "1.20", "1.30", "1.25", "1.40"])
        fall = features(["1.40", "1.30", "1.20", "1.10", "1.15", "1.00"])
        self.assertTrue(detect_rise_candidate(rise))
        self.assertFalse(detect_fall_candidate(rise))
        self.assertTrue(detect_fall_candidate(fall))
        self.assertFalse(detect_rise_candidate(fall))
        self.assertAlmostEqual(rise.efficiency, fall.efficiency)

    def test_flat_window_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            features(["1", "1", "1", "1", "1", "1"])

    def test_equal_expiry_is_a_loss_for_both_directions(self) -> None:
        entry = Decimal("100.00")
        self.assertEqual(shadow_outcome("RISE", entry, entry), "LOSS")
        self.assertEqual(shadow_outcome("FALL", entry, entry), "LOSS")


class RiseFallContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = object.__new__(RFDir5TradingBot)
        self.bot.currency = "USD"
        self.bot.app_markup_percentage = 3.0

    def test_rise_and_fall_contracts_have_no_digit_or_barrier(self) -> None:
        rise = signal("RISE")
        fall = signal("FALL")
        rise_params = self.bot._contract_parameters_for(rise, 0.50, 5)
        fall_params = self.bot._contract_parameters_for(fall, 0.50, 10)
        self.assertEqual(rise_params["contract_type"], "CALL")
        self.assertEqual(fall_params["contract_type"], "PUT")
        self.assertEqual(rise_params["duration"], 5)
        self.assertEqual(fall_params["duration"], 10)
        for params in (rise_params, fall_params):
            self.assertNotIn("barrier", params)
            self.assertNotIn("prediction", params)

    def test_direct_buy_uses_documented_markup_parameter_without_barrier(self) -> None:
        request = self.bot._direct_buy_request(signal("RISE"), 0.50)
        self.assertEqual(request["parameters"]["app_markup_percentage"], 3.0)
        self.assertNotIn("barrier", request["parameters"])

    def test_proposal_values_accept_strings_numbers_and_missing_commission(self) -> None:
        economics = parse_proposal_economics(
            {"proposal": {"id": "p1", "ask_price": "0.50", "payout": 0.92}},
            stake=0.50,
            predicted_probability=0.55,
            requested_monotonic=1.0,
            received_monotonic=1.1,
        )
        self.assertAlmostEqual(economics.potential_profit, 0.42)
        self.assertAlmostEqual(economics.potential_loss, 0.50)
        self.assertAlmostEqual(economics.break_even_probability, 0.50 / 0.92)

    def test_missing_payout_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_proposal_economics(
                {"proposal": {"id": "p1", "ask_price": "0.50"}},
                stake=0.50,
                predicted_probability=0.55,
                requested_monotonic=1.0,
                received_monotonic=1.1,
            )

    def test_recovery_debt_cannot_change_fixed_stake(self) -> None:
        self.bot.cfg = {"strategy": {"initial_stake": 0.50}}
        self.bot._client_state_for_token = lambda *_args, **_kwargs: {
            "base_stake": 0.75,
            "recovery_loss_pool": 1000.0,
            "oscar_debt": 1000.0,
            "single_recovery_pending": True,
        }
        self.assertEqual(
            self.bot._planned_stake_for_account("token", "DOT123", 0.01),
            0.75,
        )


class RFRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        config = load_test2_config(Path(__file__).with_name("config.yaml"))
        config.model.run_id = "rf-unit-test"
        self.database = Database(f"sqlite:///{Path(self.temp.name) / 'rf.db'}")
        self.database.create_schema()
        self.base = Test2Repository(self.database, config)
        self.repository = RFDir5Repository(self.base)

    def tearDown(self) -> None:
        self.database.engine.dispose()
        self.temp.cleanup()

    def create_signal_and_shadows(self):
        item = signal()
        self.repository.record_signal(item)
        self.repository.create_shadow_contracts(item, (5, 10))
        return item

    def test_five_and_ten_tick_shadows_expire_on_exact_market_ticks(self) -> None:
        item = self.create_signal_and_shadows()
        self.assertEqual(
            self.repository.settle_due_shadows(
                symbol=item.symbol,
                tick_sequence=item.tick_sequence + 4,
                expiry_quote=Decimal("101"),
            ),
            [],
        )
        first = self.repository.settle_due_shadows(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + 5,
            expiry_quote=Decimal("101"),
        )
        self.assertEqual([row["duration_ticks"] for row in first], [5])
        second = self.repository.settle_due_shadows(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + 10,
            expiry_quote=Decimal("99"),
        )
        self.assertEqual([row["duration_ticks"] for row in second], [10])

    def test_duplicate_shadow_settlement_is_idempotent(self) -> None:
        item = self.create_signal_and_shadows()
        first = self.repository.settle_due_shadows(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + 5,
            expiry_quote=Decimal("101"),
        )
        second = self.repository.settle_due_shadows(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + 5,
            expiry_quote=Decimal("101"),
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_shadow_and_demo_ledgers_are_separate(self) -> None:
        self.create_signal_and_shadows()
        with self.database.session() as session:
            self.assertEqual(session.scalar(select(func.count(ShadowContract.id))), 2)
            self.assertEqual(session.scalar(select(func.count(Trade.id))), 0)

    def test_virtual_guard_loss_win_and_restart_state_machine(self) -> None:
        first = self.create_signal_and_shadows()
        self.repository.activate_after_demo_loss()
        self.assertEqual(self.repository.guard_state()["state"], "WAITING_FOR_VIRTUAL_WIN")
        self.assertTrue(self.repository.start_virtual_contract(first.signal_id, 5))
        loss = self.repository.settle_due_shadows(
            symbol=first.symbol,
            tick_sequence=first.tick_sequence + 5,
            expiry_quote=Decimal("99"),
        )[0]
        self.assertEqual(self.repository.apply_virtual_settlement(loss), "WAITING_FOR_VIRTUAL_WIN")

        second = signal(tick_sequence=300)
        self.repository.record_signal(second)
        self.repository.create_shadow_contracts(second, (5, 10))
        self.assertTrue(self.repository.start_virtual_contract(second.signal_id, 5))
        settled = self.repository.settle_due_shadows(
            symbol=second.symbol,
            tick_sequence=second.tick_sequence + 5,
            expiry_quote=Decimal("101"),
        )
        win = next(row for row in settled if row["signal_id"] == second.signal_id)
        self.assertEqual(self.repository.apply_virtual_settlement(win), "ARMED_AFTER_VIRTUAL_WIN")
        restarted = RFDir5Repository(self.base)
        self.assertEqual(restarted.guard_state()["state"], "ARMED_AFTER_VIRTUAL_WIN")
        self.assertEqual(restarted.guard_state()["active_signal_id"], "")

    def test_stake_never_exceeds_half_percent_balance(self) -> None:
        with self.database.session() as session:
            account = ManagedAccount(label="Risk", token_secret="encrypted", enabled=True)
            session.add(account)
            session.flush()
            account_id = account.id
        stake, reason = self.repository.effective_stake(
            managed_account_id=account_id,
            current_balance=1000.0,
            requested_stake=20.0,
            maximum_balance_percent=0.5,
            daily_drawdown_percent=2.0,
            maximum_equity_drawdown_percent=5.0,
            minimum_stake=0.50,
        )
        self.assertEqual(reason, "")
        self.assertEqual(stake, 5.0)

    def test_below_minimum_risk_stake_skips_account(self) -> None:
        with self.database.session() as session:
            account = ManagedAccount(label="Small", token_secret="encrypted", enabled=True)
            session.add(account)
            session.flush()
            account_id = account.id
        stake, reason = self.repository.effective_stake(
            managed_account_id=account_id,
            current_balance=90.0,
            requested_stake=0.50,
            maximum_balance_percent=0.5,
            daily_drawdown_percent=2.0,
            maximum_equity_drawdown_percent=5.0,
            minimum_stake=0.50,
        )
        self.assertIsNone(stake)
        self.assertIn("provider minimum", reason)


class RFDecisionTests(unittest.TestCase):
    def test_keyed_bayesian_groups_never_mix(self) -> None:
        model = KeyedBayesianProbability(minimum_completed_trades=2)
        rise = BayesianGroupKey(RF_DIR5_VERSION, "1HZ100V", "RISE", 5)
        fall = BayesianGroupKey(RF_DIR5_VERSION, "1HZ100V", "FALL", 5)
        model.update(rise, True)
        model.update(rise, True)
        model.update(fall, False)
        self.assertEqual(model.counts(rise), (2, 0))
        self.assertEqual(model.counts(fall), (0, 1))

    def test_stale_signal_cannot_be_purchased(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=6,
            stale_signal_after_ms=900,
            minimum_shadow_outcomes=1000,
            required_edge_margin=0.01,
            real_gate_enabled=False,
        )
        key = BayesianGroupKey(RF_DIR5_VERSION, "1HZ100V", "RISE", 5)
        posterior = KeyedBayesianProbability().snapshot(
            key,
            break_even_probability=0.55,
        )
        economics = ProposalEconomics(
            proposal_id="p1",
            stake=0.50,
            payout=0.90,
            potential_profit=0.40,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.90,
            predicted_win_probability=0.50,
            expected_value=-0.05,
            expected_return_on_stake=-0.10,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        decision = engine.decide(
            quality_score=7,
            signal_age_ms=901,
            proposal_age_ms=1,
            proposal_economics=economics,
            shadow_snapshot=posterior,
            execution_mode="demo",
            virtual_guard_state="DEMO_LIVE",
            trading_locked=False,
        )
        self.assertEqual(decision.action, "SKIP_STALE_SIGNAL")


if __name__ == "__main__":
    unittest.main()
