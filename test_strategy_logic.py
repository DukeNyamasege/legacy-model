import asyncio
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from sqlalchemy import func, select

import enhanced_bot
from app.config import load_test2_config
from app.database import Database
from app.model.bayesian_probability import BayesianProbability
from app.model.hmm_regime import HmmInference
from app.models import ProposalRecord
from app.repositories.test2_repository import Test2Repository
from app.strategy.cooldown import AdaptiveCooldown
from app.strategy.decision_engine import (
    DecisionEngine,
    ProposalEconomics,
    parse_proposal_economics,
)
from app.strategy.over3_strategy import validate_contract_parameters
from app.strategy.signal_detector import Over3SignalDetector


def tick(digit: int, sequence: int) -> dict:
    return {
        "quote": 100 + digit / 100,
        "display": f"{100 + digit / 100:.2f}",
        "last_digit": str(digit),
        "epoch": 1_700_000_000 + sequence,
        "tick_id": f"tick-{sequence}",
    }


def pattern_ticks(offset: int = 0, digits: tuple[int, ...] = (6, 9, 0, 2, 3)) -> list[dict]:
    return [
        tick(digit, offset + sequence)
        for sequence, digit in enumerate(digits, start=1)
    ]


class SignalTests(unittest.TestCase):
    def make_detector(self) -> Over3SignalDetector:
        return Over3SignalDetector(
            run_id="test2",
            overlapping_signals_allowed=False,
            require_pattern_reset=True,
        )

    def test_bin_22001_pattern_creates_over_candidate(self) -> None:
        valid_patterns = (
            (6, 6, 0, 0, 3),
            (9, 9, 2, 2, 5),
            (7, 8, 1, 0, 4),
        )
        for digits in valid_patterns:
            detector = self.make_detector()
            signal = detector.observe(
                pattern_ticks(digits=digits),
                connection_session_id="connection-1",
                tick_sequence=5,
            )
            self.assertIsNotNone(signal)
            self.assertEqual(signal.contract_type, "DIGITOVER")
            self.assertEqual(signal.barrier, "3")
            self.assertEqual(signal.trigger_name, "BIN22001x5")
            self.assertEqual(signal.trigger_digits, digits)

    def test_near_miss_patterns_never_signal(self) -> None:
        invalid_patterns = (
            (5, 9, 0, 2, 3),
            (6, 5, 0, 2, 3),
            (6, 9, 3, 2, 3),
            (6, 9, 0, 3, 3),
            (6, 9, 0, 2, 6),
        )
        for digits in invalid_patterns:
            detector = self.make_detector()
            self.assertIsNone(
                detector.observe(
                    pattern_ticks(digits=digits),
                    connection_session_id="connection-1",
                    tick_sequence=5,
                )
            )

    def test_same_completed_window_is_suppressed_until_reset(self) -> None:
        detector = self.make_detector()
        history = pattern_ticks()
        self.assertIsNotNone(
            detector.observe(
                history, connection_session_id="connection-1", tick_sequence=5
            )
        )
        self.assertIsNone(
            detector.observe(
                history, connection_session_id="connection-1", tick_sequence=5
            )
        )

        history.append(tick(7, 6))
        self.assertIsNone(
            detector.observe(
                history, connection_session_id="connection-1", tick_sequence=6
            )
        )
        for sequence, digit in enumerate((8, 1, 2, 4), start=7):
            history.append(tick(digit, sequence))
            signal = detector.observe(
                history,
                connection_session_id="connection-1",
                tick_sequence=sequence,
            )
        self.assertEqual([int(item["last_digit"]) for item in history[-5:]], [7, 8, 1, 2, 4])
        self.assertIsNotNone(signal)


class ContractTests(unittest.TestCase):
    def test_only_exact_test2_contract_is_accepted(self) -> None:
        validate_contract_parameters(
            contract_type="DIGITOVER",
            barrier="3",
            symbol="1HZ100V",
            stake=0.35,
            duration=1,
            duration_unit="t",
        )
        invalid = [
            {"contract_type": "DIGITUNDER"},
            {"barrier": "4"},
            {"symbol": "R_100"},
            {"stake": 0.36},
            {"duration": 2},
            {"duration_unit": "s"},
        ]
        base = {
            "contract_type": "DIGITOVER",
            "barrier": "3",
            "symbol": "1HZ100V",
            "stake": 0.35,
            "duration": 1,
            "duration_unit": "t",
        }
        for change in invalid:
            with self.assertRaises(ValueError):
                validate_contract_parameters(**(base | change))

    def test_proposal_requires_current_economics(self) -> None:
        with self.assertRaises(ValueError):
            parse_proposal_economics(
                {"proposal": {"id": "p1", "ask_price": "0.35"}},
                stake=0.35,
                predicted_probability=0.6,
                requested_monotonic=time.monotonic(),
                received_monotonic=time.monotonic(),
            )
        economics = parse_proposal_economics(
            {
                "proposal": {
                    "id": "p1",
                    "ask_price": "0.35",
                    "payout": "0.55",
                }
            },
            stake=0.35,
            predicted_probability=0.65,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        self.assertAlmostEqual(economics.break_even_probability, 0.35 / 0.55)

    def test_security_scan_ignores_runtime_secret_store(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fake_token = "pat_" + ("a" * 64)
            (root / ".runtime_users.json").write_text(
                f'{{"token":"{fake_token}"}}', encoding="utf-8"
            )
            (root / "safe.py").write_text("VALUE = 1\n", encoding="utf-8")
            enhanced_bot.scan_source_for_hardcoded_tokens(root)
            (root / "unsafe.py").write_text(
                f'TOKEN = "{fake_token}"\n', encoding="utf-8"
            )
            with self.assertRaises(RuntimeError):
                enhanced_bot.scan_source_for_hardcoded_tokens(root)


class TimingAndModelTests(unittest.TestCase):
    def test_new_tick_makes_candidate_stale(self) -> None:
        detector = Over3SignalDetector(
            run_id="test2",
            overlapping_signals_allowed=False,
            require_pattern_reset=True,
        )
        signal = detector.observe(
            pattern_ticks(),
            connection_session_id="connection-1",
            tick_sequence=5,
        )
        bayesian_model = BayesianProbability(
            prior_alpha=3,
            prior_beta=2,
            credible_interval=0.95,
            minimum_completed_trades=300,
        )
        bayesian = bayesian_model.snapshot(0.63, 0.02)
        economics = ProposalEconomics(
            proposal_id="p1",
            stake=0.35,
            payout=0.55,
            potential_profit=0.20,
            potential_loss=0.35,
            break_even_probability=0.35 / 0.55,
            predicted_win_probability=bayesian.posterior_mean,
            expected_value=-0.02,
            expected_return_on_stake=-0.05,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        engine = DecisionEngine(
            maximum_signal_age_ms=900,
            maximum_proposal_age_ms=900,
            bayesian_mode="shadow",
            bayesian_confidence_threshold=0.95,
            hmm_mode="shadow",
            favourable_state="MEAN_REVERSION",
            favourable_state_threshold=0.70,
        )
        decision = engine.decide(
            signal=signal,
            economics=economics,
            bayesian=bayesian,
            hmm=HmmInference(False, "NOT_READY", {}, 0),
            current_tick_sequence=3,
            connection_session_id="connection-1",
            connection_healthy=True,
            pattern_reset_required=False,
        )
        self.assertEqual(decision.final_action, "SKIP_STALE_SIGNAL")

    def test_shadow_mode_does_not_block_negative_ev(self) -> None:
        self.assertGreater(
            BayesianProbability(
                prior_alpha=3,
                prior_beta=2,
                credible_interval=0.95,
                minimum_completed_trades=300,
            ).snapshot(0.64, 0.02).posterior_mean,
            0,
        )

    def test_adaptive_cooldown_has_no_hard_session_stop(self) -> None:
        cooldown = AdaptiveCooldown(
            after_win_ticks=1,
            after_loss_ticks=3,
            after_three_consecutive_losses_ticks=15,
            after_five_consecutive_losses_ticks=50,
        )
        for _ in range(5):
            state = cooldown.register_outcome("loss")
        self.assertEqual(state.ticks_remaining, 50)
        self.assertEqual(state.consecutive_losses, 5)


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        raw["storage"]["local_database_url"] = (
            "sqlite:///" + (self.root / "test2.db").as_posix()
        )
        raw["files"]["tokens"] = (self.root / "tokens.txt").as_posix()
        raw["files"]["state"] = (self.root / "state.json").as_posix()
        raw["files"]["users"] = (self.root / "users.json").as_posix()
        raw["logging"]["file"] = (self.root / "bot.log").as_posix()
        self.config_path = self.root / "config.yaml"
        self.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        self.config = load_test2_config(self.config_path)
        self.database = Database(self.config.database_url)
        self.database.create_schema()
        self.repository = Test2Repository(self.database, self.config)

    def tearDown(self) -> None:
        self.database.engine.dispose()
        self.temp.cleanup()

    def test_signal_can_only_be_consumed_once(self) -> None:
        detector = Over3SignalDetector(
            run_id="test2",
            overlapping_signals_allowed=False,
            require_pattern_reset=True,
        )
        signal = detector.observe(
            pattern_ticks(),
            connection_session_id="connection-1",
            tick_sequence=5,
        )
        self.repository.record_candidate(signal)
        self.assertTrue(self.repository.consume_signal(signal.signal_id))
        self.assertFalse(self.repository.consume_signal(signal.signal_id))

    def test_second_worker_cannot_take_healthy_lease(self) -> None:
        values = {
            "lease_key": "test2:demo:account",
            "host_name": "host",
            "process_id": 1,
            "deployment_id": "test",
        }
        self.assertTrue(self.repository.acquire_lease(worker_id="one", **values))
        self.assertFalse(self.repository.acquire_lease(worker_id="two", **values))

    def test_account_balance_is_masked_and_updated(self) -> None:
        self.repository.update_account_balance(
            account_id="DOT90000001",
            balance=9999.50,
            currency="USD",
        )
        summary = self.repository.summary()
        self.assertEqual(summary["accounts"][0]["account"], "DOT***001")
        self.assertEqual(summary["account_balance_total"], 9999.50)

    def test_deriv_may_reuse_proposal_id_for_identical_terms(self) -> None:
        signals = []
        for offset in (0, 10):
            detector = Over3SignalDetector(
                run_id="test2",
                overlapping_signals_allowed=False,
                require_pattern_reset=True,
            )
            signal = detector.observe(
                pattern_ticks(offset=offset),
                connection_session_id=f"connection-{offset}",
                tick_sequence=5 + offset,
            )
            self.repository.record_candidate(signal)
            signals.append(signal)
        economics = ProposalEconomics(
            proposal_id="reused-by-deriv",
            stake=0.35,
            payout=0.55,
            potential_profit=0.20,
            potential_loss=0.35,
            break_even_probability=0.35 / 0.55,
            predicted_win_probability=0.60,
            expected_value=-0.02,
            expected_return_on_stake=-0.05,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        for signal in signals:
            self.repository.record_proposal(signal, economics)
        with self.database.session() as session:
            count = session.scalar(
                select(func.count()).select_from(ProposalRecord).where(
                    ProposalRecord.proposal_id == "reused-by-deriv"
                )
            )
        self.assertEqual(count, 2)

    def test_settlement_is_atomic_and_idempotent(self) -> None:
        detector = Over3SignalDetector(
            run_id="test2",
            overlapping_signals_allowed=False,
            require_pattern_reset=True,
        )
        signal = detector.observe(
            pattern_ticks(),
            connection_session_id="connection-1",
            tick_sequence=5,
        )
        self.repository.record_candidate(signal)
        self.repository.consume_signal(signal.signal_id)
        self.repository.register_purchase(
            signal_id=signal.signal_id,
            contract_id="12345",
            transaction_id="67890",
            account_id="DOT90000001",
            purchase_time=datetime.now(timezone.utc),
            aligned_with_signal=True,
        )
        self.assertTrue(
            self.repository.settle_trade(
                contract_id="12345",
                profit=0.20,
                outcome="win",
                entry_tick=100.04,
                exit_tick=100.08,
                exit_digit=8,
            )
        )
        self.assertFalse(
            self.repository.settle_trade(
                contract_id="12345",
                profit=0.20,
                outcome="win",
                entry_tick=100.04,
                exit_tick=100.08,
                exit_digit=8,
            )
        )
        summary = self.repository.summary()
        self.assertEqual(summary["wins"], 1)
        self.assertAlmostEqual(summary["net_profit"], 0.20)


class BotSignalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_schedules_only_the_exact_bin_22001_pattern(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
            token_path = root / "tokens.txt"
            token_path.write_text("test-token\n", encoding="utf-8")
            raw["files"] = {
                "tokens": token_path.as_posix(),
                "state": (root / "state.json").as_posix(),
                "users": (root / "users.json").as_posix(),
            }
            raw["logging"]["file"] = (root / "bot.log").as_posix()
            raw["storage"]["local_database_url"] = (
                "sqlite:///" + (root / "test2.db").as_posix()
            )
            path = root / "config.yaml"
            path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            bot = enhanced_bot.TradingBot(str(path))
            bot.connection_session_id = "connection-1"
            bot.public_client.is_connected = True
            bot.repository.set_status("RUNNING")
            bot._render_live_ticks = lambda note="": None
            spawned: list[str] = []

            def capture_task(coroutine, *, name: str) -> None:
                spawned.append(name)
                coroutine.close()

            bot._spawn_background_task = capture_task
            for sequence, digit in enumerate([8, 9, 0, 3, 3], start=1):
                await bot._on_tick(
                    {
                        "tick": {
                            "quote": 100 + digit / 100,
                            "epoch": 1_700_000_000 + sequence,
                            "id": f"tick-{sequence}",
                            "symbol": "1HZ100V",
                        }
                    }
                )
            self.assertEqual(spawned, [])

            for sequence, digit in enumerate([6, 9, 0, 2, 3], start=6):
                await bot._on_tick(
                    {
                        "tick": {
                            "quote": 100 + digit / 100,
                            "epoch": 1_700_000_000 + sequence,
                            "id": f"tick-{sequence}",
                            "symbol": "1HZ100V",
                        }
                    }
                )
            self.assertEqual(len(spawned), 1)
            self.assertIn("purchase_", spawned[0])
            bot.database.engine.dispose()
            for handler in list(bot.logger.handlers):
                handler.close()
            bot.logger.handlers.clear()


if __name__ == "__main__":
    unittest.main()
