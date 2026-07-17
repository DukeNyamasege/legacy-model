import asyncio
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

import yaml
from sqlalchemy import func, select

import enhanced_bot
from app.config import load_test2_config
from app.database import Database
from app.dashboard_metrics import build_execution_summary
from app.model.bayesian_probability import BayesianProbability, BayesianSnapshot
from app.model.hmm_regime import HmmInference
from app.models import ProposalRecord
from app.repositories.test2_repository import Test2Repository
from app.strategy.cooldown import AdaptiveCooldown
from app.strategy.decision_engine import (
    DecisionEngine,
    ProposalEconomics,
    parse_proposal_economics,
)
from app.strategy.over2_strategy import TEST2_SYMBOLS, validate_contract_parameters
from app.strategy.signal_detector import Over2SignalDetector
from scripts.reset_test_data import reset_database

os.environ.setdefault("COPYTRADING_ALLOW_LEGACY_GLOBAL_TOKENS", "true")


def tick(digit: int, sequence: int) -> dict:
    return {
        "quote": 100 + digit / 100,
        "display": f"{100 + digit / 100:.2f}",
        "last_digit": str(digit),
        "epoch": 1_700_000_000 + sequence,
        "tick_id": f"tick-{sequence}",
    }


def pattern_ticks(
    offset: int = 0,
    digits: tuple[int, ...] = (6, 8, 0, 2, 4),
) -> list[dict]:
    return [
        tick(digit, offset + sequence)
        for sequence, digit in enumerate(digits, start=1)
    ]


def live_tick_payload(
    sequence: int,
    quote: float,
    symbol: str = "1HZ100V",
) -> dict:
    return {
        "tick": {
            "quote": quote,
            "epoch": 1_700_000_000 + sequence,
            "id": f"tick-{sequence}",
            "symbol": symbol,
        }
    }


class DashboardMetricsTests(unittest.TestCase):
    def test_global_cards_use_master_stats_and_all_account_profit(self) -> None:
        master = {
            "account": "DOT***422",
            "balance": 389.16,
            "currency": "USD",
            "trades": 4,
            "wins": 3,
            "losses": 1,
            "win_rate": 0.75,
            "profit": 1.25,
            "longest_win_streak": 3,
            "longest_loss_streak": 1,
            "open_trades": 0,
            "oldest_open_trade_seconds": 0,
        }
        copier = {"account": "DOT***967", "trades": 3, "profit": 0.75}
        stopped = {"account": "DOT***546", "trades": 4, "profit": -0.50}

        result = build_execution_summary(
            {
                "purchased_trades": 571,
                "wins": 437,
                "losses": 134,
                "net_profit": 46.17,
                "max_open_trade_seconds": 6,
            },
            active_accounts=[master, copier],
            linked_accounts=[master, copier, stopped],
            master=master,
        )

        self.assertEqual(result["total_traders"], 2)
        self.assertEqual(result["purchased_trades"], 4)
        self.assertEqual(result["wins"], 3)
        self.assertEqual(result["losses"], 1)
        self.assertEqual(result["longest_win_streak"], 3)
        self.assertEqual(result["primary_account_balance"], 389.16)
        self.assertEqual(result["all_accounts_profit"], 1.50)
        self.assertEqual(result["copy_trade_gap"], 1)


class SignalTests(unittest.TestCase):
    def make_detector(self) -> Over2SignalDetector:
        return Over2SignalDetector(
            run_id="test2",
            overlapping_signals_allowed=False,
            require_pattern_reset=True,
        )

    def test_bin_22001_pattern_creates_over2_candidate(self) -> None:
        valid_patterns = (
            (6, 6, 0, 0, 3),
            (9, 8, 2, 1, 5),
            (7, 9, 1, 2, 4),
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
            self.assertEqual(signal.barrier, "2")
            self.assertEqual(signal.trigger_name, "BIN22001x5")
            self.assertEqual(signal.trigger_digits, digits)

    def test_candidate_keeps_the_market_that_emitted_the_pattern(self) -> None:
        detector = Over2SignalDetector(run_id="test2", symbol="R_10")
        signal = detector.observe(
            pattern_ticks(),
            connection_session_id="connection-1",
            tick_sequence=5,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.symbol, "R_10")

    def test_near_miss_patterns_never_signal(self) -> None:
        invalid_patterns = (
            (5, 8, 0, 2, 4),
            (6, 5, 0, 2, 4),
            (6, 8, 3, 2, 4),
            (6, 8, 0, 3, 4),
            (6, 8, 0, 2, 6),
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
        for sequence, digit in enumerate((8, 9, 1, 2, 4), start=7):
            history.append(tick(digit, sequence))
            signal = detector.observe(
                history,
                connection_session_id="connection-1",
                tick_sequence=sequence,
            )
        self.assertEqual(
            [int(item["last_digit"]) for item in history[-5:]],
            [8, 9, 1, 2, 4],
        )
        self.assertIsNotNone(signal)


class ContractTests(unittest.TestCase):
    def test_only_exact_test2_contract_is_accepted(self) -> None:
        for symbol in TEST2_SYMBOLS:
            validate_contract_parameters(
                contract_type="DIGITOVER",
                barrier="2",
                symbol=symbol,
                stake=0.50,
                duration=1,
                duration_unit="t",
            )
        invalid = [
            {"contract_type": "DIGITUNDER"},
            {"barrier": "3"},
            {"symbol": "R_UNKNOWN"},
            {"stake": 0.10},
            {"duration": 2},
            {"duration_unit": "s"},
        ]
        base = {
            "contract_type": "DIGITOVER",
            "barrier": "2",
            "symbol": "1HZ100V",
            "stake": 0.50,
            "duration": 1,
            "duration_unit": "t",
        }
        for change in invalid:
            with self.assertRaises(ValueError):
                validate_contract_parameters(**(base | change))

    def test_proposal_requires_current_economics(self) -> None:
        with self.assertRaises(ValueError):
            parse_proposal_economics(
                {"proposal": {"id": "p1", "ask_price": "0.50"}},
                stake=0.50,
                predicted_probability=0.6,
                requested_monotonic=time.monotonic(),
                received_monotonic=time.monotonic(),
            )
        economics = parse_proposal_economics(
            {
                "proposal": {
                    "id": "p1",
                    "ask_price": "0.50",
                    "payout": "0.95",
                }
            },
            stake=0.50,
            predicted_probability=0.65,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        self.assertAlmostEqual(economics.break_even_probability, 0.50 / 0.95)

    def test_public_proposal_economics_reserve_expected_app_markup(self) -> None:
        economics = parse_proposal_economics(
            {
                "proposal": {
                    "id": "p-marked",
                    "ask_price": "0.50",
                    "payout": "0.69",
                }
            },
            stake=0.50,
            predicted_probability=0.85,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
            app_markup_percentage=3.0,
        )
        expected_markup = 0.69 * 0.03
        self.assertAlmostEqual(economics.payout, 0.69)
        self.assertAlmostEqual(economics.potential_profit, 0.69 - 0.50 - expected_markup)
        self.assertAlmostEqual(economics.potential_loss, 0.50 + expected_markup)
        self.assertAlmostEqual(
            economics.break_even_probability,
            (0.50 + expected_markup) / 0.69,
        )

    def test_direct_buy_places_markup_only_in_documented_parameters(self) -> None:
        signal = Over2SignalDetector(run_id="test2").observe(
            pattern_ticks(),
            connection_session_id="connection-1",
            tick_sequence=5,
        )
        bot = enhanced_bot.TradingBot.__new__(enhanced_bot.TradingBot)
        bot.currency = "USD"
        bot.duration = 1
        bot.duration_unit = "t"
        bot.app_markup_percentage = 3.0

        request = bot._direct_buy_request(signal, 0.50)

        self.assertEqual(request["buy"], "1")
        self.assertEqual(request["price"], 0.50)
        self.assertNotIn("app_markup_percentage", request)
        self.assertEqual(request["parameters"]["app_markup_percentage"], 3.0)
        self.assertEqual(request["parameters"]["duration"], 1)
        self.assertEqual(request["parameters"]["duration_unit"], "t")

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
        detector = Over2SignalDetector(
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
            stake=0.50,
            payout=0.95,
            potential_profit=0.45,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.95,
            predicted_win_probability=bayesian.posterior_mean,
            expected_value=-0.02,
            expected_return_on_stake=-0.05,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        engine = DecisionEngine(
            reject_if_new_tick_arrives=True,
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
            current_tick_sequence=6,
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

    def test_calibrated_gate_accepts_positive_markup_adjusted_entry(self) -> None:
        detector = Over2SignalDetector(run_id="test2")
        signal = detector.observe(
            pattern_ticks(),
            connection_session_id="connection-1",
            tick_sequence=5,
        )
        model = BayesianProbability(
            prior_alpha=108,
            prior_beta=19,
            credible_interval=0.95,
            minimum_completed_trades=0,
        )
        preliminary = model.snapshot(0.75, 0.02)
        economics = parse_proposal_economics(
            {"proposal": {"id": "p-gated", "ask_price": 0.50, "payout": 0.69}},
            stake=0.50,
            predicted_probability=preliminary.posterior_mean,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
            app_markup_percentage=3.0,
        )
        bayesian = model.snapshot(economics.break_even_probability, 0.02)
        decision = DecisionEngine(
            reject_if_new_tick_arrives=False,
            maximum_signal_age_ms=900,
            maximum_proposal_age_ms=900,
            bayesian_mode="gate",
            bayesian_confidence_threshold=0.95,
            hmm_mode="shadow",
            favourable_state="MEAN_REVERSION",
            favourable_state_threshold=0.70,
        ).decide(
            signal=signal,
            economics=economics,
            bayesian=bayesian,
            hmm=HmmInference(False, "NOT_READY", {}, 0),
            current_tick_sequence=5,
            connection_session_id="connection-1",
            connection_healthy=True,
            pattern_reset_required=False,
        )
        self.assertEqual(decision.final_action, "PURCHASE")
        self.assertGreater(decision.expected_value, 0)

    def test_positive_ev_can_pass_relaxed_bayesian_confidence_gate(self) -> None:
        detector = Over2SignalDetector(run_id="test2")
        signal = detector.observe(
            pattern_ticks(),
            connection_session_id="connection-1",
            tick_sequence=5,
        )
        bayesian = BayesianSnapshot(
            prior_alpha=108.0,
            prior_beta=19.0,
            observed_wins=29,
            observed_losses=6,
            posterior_alpha=137.0,
            posterior_beta=25.0,
            posterior_mean=0.8457,
            lower_credible_bound=0.0,
            upper_credible_bound=1.0,
            probability_above_break_even=0.60,
            probability_above_safety_threshold=0.40,
            ready=True,
        )
        economics = ProposalEconomics(
            proposal_id="p-relaxed",
            stake=0.50,
            payout=0.69,
            potential_profit=0.1693,
            potential_loss=0.5207,
            break_even_probability=0.7546,
            predicted_win_probability=0.8115,
            expected_value=0.0392,
            expected_return_on_stake=0.0784,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        decision = DecisionEngine(
            reject_if_new_tick_arrives=False,
            maximum_signal_age_ms=900,
            maximum_proposal_age_ms=900,
            bayesian_mode="gate",
            bayesian_confidence_threshold=0.95,
            hmm_mode="shadow",
            favourable_state="MEAN_REVERSION",
            favourable_state_threshold=0.70,
        ).decide(
            signal=signal,
            economics=economics,
            bayesian=bayesian,
            hmm=HmmInference(False, "NOT_READY", {}, 0),
            current_tick_sequence=5,
            connection_session_id="connection-1",
            connection_healthy=True,
            pattern_reset_required=False,
        )
        self.assertEqual(decision.final_action, "PURCHASE")
        self.assertNotIn("SKIP_BAYESIAN_EDGE_INSUFFICIENT", decision.rejection_reasons)

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

    def test_cumulative_recovery_keeps_debt_until_a_win(self) -> None:
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
            try:
                token = next(iter(bot.clients))
                bot.valid_clients = [(token, "DOT90000001")]
                state = next(iter(bot.clients.values()))
                state["last_profit_ratio"] = 0.45 / 0.50
                bot._update_client_recovery_state(state, outcome="loss", profit=-0.50)
                self.assertTrue(state["single_recovery_pending"])
                self.assertFalse(state["single_recovery_active"])
                self.assertEqual(state["current_stake"], 0.50)
                self.assertAlmostEqual(state["oscar_debt"], 0.50)
                self.assertEqual(state["recovery_wins_remaining"], 2)

                self.assertEqual(bot._planned_stake_for_accounts(0.45 / 0.50), 0.50)
                bot._update_client_recovery_state(state, outcome="loss", profit=-0.50)
                self.assertTrue(state["single_recovery_pending"])
                self.assertFalse(state["single_recovery_active"])
                self.assertEqual(state["current_stake"], 0.56)
                self.assertEqual(state["loss_streak"], 2)
                self.assertEqual(state["recovery_loss_pool"], 1.00)

                bot._update_client_recovery_state(state, outcome="win", profit=1.06)
                self.assertEqual(state["current_stake"], 0.50)
                self.assertEqual(state["recovery_loss_pool"], 0.0)
                self.assertFalse(state["single_recovery_pending"])
                self.assertFalse(state["single_recovery_active"])
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()

    def test_fifth_loss_plans_two_win_recovery_stake(self) -> None:
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
            try:
                state = next(iter(bot.clients.values()))
                state["last_profit_ratio"] = 0.45 / 0.50
                for _ in range(5):
                    stake = float(state["current_stake"])
                    bot._update_client_recovery_state(
                        state,
                        outcome="loss",
                        profit=-stake,
                    )

                self.assertEqual(state["loss_streak"], 5)
                self.assertEqual(state["recovery_loss_pool"], 3.78)
                self.assertEqual(state["current_stake"], 2.10)
                self.assertEqual(state["recovery_wins_remaining"], 2)
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()

    def test_duplicate_account_only_gets_one_purchase_slot(self) -> None:
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
            old_include_master = os.environ.get("COPYTRADING_INCLUDE_MASTER")
            os.environ["COPYTRADING_INCLUDE_MASTER"] = "true"
            try:
                bot.valid_clients = [
                    ("token-a", "DOT90000001"),
                    ("token-b", "DOT90000001"),
                    ("token-c", "DOT90000002"),
                ]
                bot.sessions = {
                    token: MagicMock(is_connected=True)
                    for token in ("token-a", "token-b", "token-c")
                }
                self.assertEqual(
                    bot._eligible_purchase_accounts(),
                    [
                        ("token-a", "DOT90000001"),
                        ("token-c", "DOT90000002"),
                    ],
                )
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()
                if old_include_master is None:
                    os.environ.pop("COPYTRADING_INCLUDE_MASTER", None)
                else:
                    os.environ["COPYTRADING_INCLUDE_MASTER"] = old_include_master

    def test_oauth_accounts_are_not_bulk_purchase_capable(self) -> None:
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
            try:
                bot.user_profiles = {
                    "oauth-token": {"auth_type": "oauth"},
                    "pat-token": {"auth_type": "pat"},
                    "global-token": {"auth_type": "global_token"},
                }
                self.assertFalse(bot._bulk_purchase_token_capable("oauth-token"))
                self.assertTrue(bot._bulk_purchase_token_capable("pat-token"))
                self.assertTrue(bot._bulk_purchase_token_capable("global-token"))
                self.assertEqual(
                    bot._bulk_purchase_incompatible_accounts(
                        [
                            ("oauth-token", "DOT90000001"),
                            ("pat-token", "DOT90000002"),
                        ]
                    ),
                    ["DOT90000001"],
                )
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()

    def test_sanitize_account_ids_masks_provider_errors(self) -> None:
        message = (
            'Token or account validation failed for account "DOT91317422"; '
            "account CR123456 failed"
        )
        sanitized = enhanced_bot.sanitize_account_ids(message)
        self.assertNotIn("DOT91317422", sanitized)
        self.assertNotIn("CR123456", sanitized)
        self.assertIn("DOT***422", sanitized)
        self.assertIn("CR1***456", sanitized)

    def test_single_account_uses_direct_markup_transport(self) -> None:
        bot = enhanced_bot.TradingBot.__new__(enhanced_bot.TradingBot)

        self.assertTrue(
            bot._requires_private_purchase_transport(
                account_count=1,
                bulk_incompatible_accounts=[],
            )
        )
        self.assertFalse(
            bot._requires_private_purchase_transport(
                account_count=2,
                bulk_incompatible_accounts=[],
            )
        )
        self.assertTrue(
            bot._requires_private_purchase_transport(
                account_count=2,
                bulk_incompatible_accounts=["DOT90000001"],
            )
        )

    def test_all_legacy_copy_failure_pauses_are_cleared(self) -> None:
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
            try:
                bot.repository.set_status("MANUAL_PAUSE", "BULK_PURCHASE_REQUIRED")
                bot._sync_running_status_after_validation()
                self.assertEqual(bot.repository.control_state(), ("RUNNING", ""))

                bot.repository.set_status("MANUAL_PAUSE", "COPY_PURCHASE_PARTIAL")
                bot._sync_running_status_after_validation()
                self.assertEqual(
                    bot.repository.control_state(),
                    ("RUNNING", ""),
                )

                bot.repository.set_status("MANUAL_PAUSE", "ADMIN_REQUEST")
                bot._sync_running_status_after_validation()
                self.assertEqual(
                    bot.repository.control_state(),
                    ("MANUAL_PAUSE", "ADMIN_REQUEST"),
                )

                bot.repository.set_status("EMERGENCY_STOP", "ADMIN_REQUEST")
                bot._sync_running_status_after_validation()
                self.assertEqual(bot.repository.control_state(), ("RUNNING", ""))
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()


class AccountIsolationTests(unittest.IsolatedAsyncioTestCase):
    def test_permanent_credential_errors_are_distinct_from_timeouts(self) -> None:
        self.assertTrue(
            enhanced_bot.is_permanent_credential_error(
                {"code": "InvalidToken", "message": "Invalid or expired token"}
            )
        )
        self.assertFalse(
            enhanced_bot.is_permanent_credential_error(
                {"code": "TIMEOUT", "message": "Request timed out"}
            )
        )

    async def test_invalid_token_is_quarantined_without_blocking_other_accounts(self) -> None:
        bot = enhanced_bot.TradingBot.__new__(enhanced_bot.TradingBot)
        bot.repository = MagicMock()
        bot.repository.runtime_mode.return_value = "demo"
        bot._load_runtime_accounts = MagicMock(
            return_value=(
                ["invalid-token", "healthy-token"],
                {
                    "invalid-token": {
                        "managed_account_id": 60,
                        "account_id": "DOT90000060",
                    },
                    "healthy-token": {
                        "managed_account_id": 10,
                        "account_id": "DOT90000010",
                    },
                },
            )
        )
        bot._set_account_execution_status = MagicMock()
        bot._sync_running_status_after_validation = MagicMock()
        bot.logger = MagicMock()
        bot.app_id = "app-id"
        bot.rest_base_url = "https://example.invalid"
        bot.valid_clients = []

        responses = [
            {"error": {"code": "InvalidToken", "message": "Invalid or expired token"}},
            {
                "data": [
                    {
                        "account_id": "DOT90000010",
                        "account_type": "demo",
                        "balance": 100.0,
                        "currency": "USD",
                        "status": "active",
                    }
                ]
            },
        ]
        with patch("enhanced_bot._rest_request", new=AsyncMock(side_effect=responses)):
            await bot.validate_accounts()

        self.assertEqual(bot.valid_clients, [("healthy-token", "DOT90000010")])
        bot._set_account_execution_status.assert_any_call(
            60,
            "credential_error",
            "Invalid or expired token",
        )
        bot.repository.update_account_balance.assert_called_once()

    async def test_each_account_uses_its_own_configured_base_stake(self) -> None:
        bot = enhanced_bot.TradingBot.__new__(enhanced_bot.TradingBot)
        bot.cfg = {"strategy": {"initial_stake": 0.50}}
        bot.recovery_cfg = MagicMock(maximum_stake=1000.0, recovery_runs=2)
        bot.user_profiles = {}
        bot.clients = {
            "token-a": {
                "account_id": "DOT90000001",
                "base_stake": 0.50,
                "recovery_loss_pool": 0.0,
                "single_recovery_pending": False,
            },
            "token-b": {
                "account_id": "DOT90000002",
                "base_stake": 2.00,
                "recovery_loss_pool": 0.0,
                "single_recovery_pending": False,
            },
        }

        self.assertEqual(
            bot._planned_stake_for_account("token-a", "DOT90000001", 0.40),
            0.50,
        )
        self.assertEqual(
            bot._planned_stake_for_account("token-b", "DOT90000002", 0.40),
            2.00,
        )

    async def test_disconnected_account_is_skipped_without_blocking_healthy_account(self) -> None:
        bot = enhanced_bot.TradingBot.__new__(enhanced_bot.TradingBot)
        bot.valid_clients = [
            ("healthy-token", "DOT90000001"),
            ("broken-token", "DOT90000002"),
        ]
        bot.sessions = {
            "healthy-token": MagicMock(is_connected=True),
            "broken-token": MagicMock(is_connected=False),
        }
        bot.logger = MagicMock()

        with patch.dict(os.environ, {"COPYTRADING_INCLUDE_MASTER": "true"}):
            eligible = bot._eligible_purchase_accounts()

        self.assertEqual(eligible, [("healthy-token", "DOT90000001")])

    async def test_stake_group_failure_returns_errors_only_for_that_group(self) -> None:
        bot = enhanced_bot.TradingBot.__new__(enhanced_bot.TradingBot)
        bot.logger = MagicMock()
        signal = MagicMock(signal_id="signal-1")

        async def purchase_group(*, signal, eligible_accounts, stake_amount):
            if stake_amount == 2.00:
                raise RuntimeError("isolated account failure")
            return [
                {
                    "account_id": eligible_accounts[0][1],
                    "contract_id": "123",
                }
            ]

        bot._purchase_stake_group = AsyncMock(side_effect=purchase_group)
        transactions = await bot._purchase_accounts_by_stake(
            signal=signal,
            eligible_accounts=[
                ("token-a", "DOT90000001"),
                ("token-b", "DOT90000002"),
            ],
            stake_by_token={"token-a": 0.50, "token-b": 2.00},
        )

        self.assertEqual(transactions[0]["contract_id"], "123")
        self.assertEqual(transactions[0]["stake_amount"], 0.50)
        self.assertIn("error", transactions[1])
        self.assertEqual(transactions[1]["account_id"], "DOT90000002")

    async def test_take_profit_disables_only_the_account_that_reached_it(self) -> None:
        bot = enhanced_bot.TradingBot.__new__(enhanced_bot.TradingBot)
        bot.repository = MagicMock()
        bot.repository.account_summary.return_value = {"profit": 5.00}
        bot.logger = MagicMock()
        bot.valid_clients = [
            ("token-a", "DOT90000001"),
            ("token-b", "DOT90000002"),
        ]
        state = {
            "managed_account_id": 10,
            "take_profit": 5.00,
            "stop_loss": 0.00,
        }

        result = bot._enforce_account_risk_limit(
            "token-a",
            "DOT90000001",
            state,
        )

        self.assertEqual(result, "take_profit")
        self.assertEqual(bot.valid_clients, [("token-b", "DOT90000002")])
        bot.repository.set_managed_account_enabled.assert_called_once_with(10, False)
        bot.repository.set_status.assert_not_called()


class LeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_worker_waits_for_stale_lease(self) -> None:
        bot = enhanced_bot.TradingBot.__new__(enhanced_bot.TradingBot)
        bot.is_running = True
        bot.lease_key = "bin22001:demo:test"
        bot.worker_id = "replacement-worker"
        bot._lease_owned = False
        bot.logger = MagicMock()
        bot.repository = MagicMock()
        bot.repository.acquire_lease.side_effect = [False, True]

        with patch("enhanced_bot.asyncio.sleep", new=AsyncMock()) as sleep:
            acquired = await bot._wait_for_trader_lease(retry_seconds=0.1)

        self.assertTrue(acquired)
        self.assertTrue(bot._lease_owned)
        self.assertEqual(bot.repository.acquire_lease.call_count, 2)
        sleep.assert_awaited_once_with(0.1)
        bot.logger.warning.assert_called_once()

    async def test_lost_heartbeat_uses_reconnecting_not_emergency_stop(self) -> None:
        bot = enhanced_bot.TradingBot.__new__(enhanced_bot.TradingBot)
        bot.is_running = True
        bot.lease_key = "bin22001:demo:test"
        bot.worker_id = "worker-a"
        bot.connection_session_id = "connection-1"
        bot._lease_owned = True
        bot.logger = MagicMock()
        bot.repository = MagicMock()
        bot.repository.acquire_lease.return_value = False

        await bot._lease_heartbeat_loop()

        self.assertFalse(bot.is_running)
        self.assertFalse(bot._lease_owned)
        bot.repository.set_status.assert_called_once_with(
            "RECONNECTING",
            "TRADER_LOCK_LOST",
        )


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
        detector = Over2SignalDetector(
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

    def test_personal_controls_persist_without_changing_auto_trade(self) -> None:
        account = self.repository.add_managed_account(
            label="Account DOT***001",
            token_secret="encrypted-token-placeholder",
            enabled=True,
        )
        settings = self.repository.update_account_execution_settings(
            account["id"],
            stake_amount=1.25,
            take_profit=12.50,
            stop_loss=4.00,
        )
        stored = self.repository.managed_account(account["id"])

        self.assertEqual(settings["stake_amount"], 1.25)
        self.assertEqual(settings["take_profit"], 12.50)
        self.assertEqual(settings["stop_loss"], 4.00)
        self.assertTrue(stored["enabled"])

    def test_trade_reset_preserves_credentials_sessions_controls_and_enabled_state(self) -> None:
        account = self.repository.add_managed_account(
            label="Account DOT***001",
            token_secret="encrypted-token-placeholder",
            enabled=True,
        )
        self.repository.update_account_execution_settings(
            account["id"],
            stake_amount=1.25,
            take_profit=12.50,
            stop_loss=4.00,
        )
        self.repository.create_client_session(
            session_hash="session-hash",
            managed_account_id=account["id"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )

        reset_database(self.database, self.config.model.run_id)
        repository = Test2Repository(self.database, self.config)
        stored = repository.managed_account(account["id"])
        session_account = repository.client_session_account("session-hash")

        self.assertEqual(stored["token_secret"], "encrypted-token-placeholder")
        self.assertTrue(stored["enabled"])
        self.assertEqual(stored["stake_amount"], 1.25)
        self.assertEqual(stored["take_profit"], 12.50)
        self.assertEqual(stored["stop_loss"], 4.00)
        self.assertIsNotNone(session_account)
        self.assertEqual(repository.summary()["purchased_trades"], 0)

    def test_deriv_may_reuse_proposal_id_for_identical_terms(self) -> None:
        signals = []
        for offset in (0, 10):
            detector = Over2SignalDetector(
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
            stake=0.50,
            payout=0.95,
            potential_profit=0.45,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.95,
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
        detector = Over2SignalDetector(
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
        provider_purchase_time = datetime.now(timezone.utc)
        provider_settlement_time = datetime.fromtimestamp(
            provider_purchase_time.timestamp() + 1,
            timezone.utc,
        )
        self.repository.register_purchase(
            signal_id=signal.signal_id,
            contract_id="12345",
            transaction_id="67890",
            account_id="DOT90000001",
            purchase_time=datetime.now(timezone.utc),
            aligned_with_signal=True,
            buy_price=0.50,
            payout=0.67,
            provider_purchase_time=provider_purchase_time,
            provider_start_time=provider_purchase_time,
            contract_duration=1,
            contract_duration_unit="t",
        )
        self.repository.register_purchase(
            signal_id=signal.signal_id,
            contract_id="12346",
            transaction_id="67891",
            account_id="DOT90000002",
            purchase_time=datetime.now(timezone.utc),
            aligned_with_signal=True,
            buy_price=0.50,
            payout=0.67,
        )
        self.assertTrue(
            self.repository.settle_trade(
                contract_id="12345",
                profit=0.20,
                outcome="win",
                entry_tick=100.04,
                exit_tick=100.08,
                exit_digit=8,
                buy_price=0.50,
                payout=0.67,
                app_markup_amount=0.02,
                commission=0.02,
                provider_purchase_time=provider_purchase_time,
                provider_start_time=provider_purchase_time,
                provider_expiry_time=provider_settlement_time,
                provider_settlement_time=provider_settlement_time,
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
                buy_price=0.50,
                payout=0.67,
                app_markup_amount=0.02,
                commission=0.02,
            )
        )
        self.assertTrue(
            self.repository.settle_trade(
                contract_id="12346",
                profit=0.20,
                outcome="win",
                entry_tick=100.04,
                exit_tick=100.08,
                exit_digit=8,
                buy_price=0.50,
                payout=0.67,
                app_markup_amount=0.02,
                commission=0.02,
            )
        )
        summary = self.repository.summary()
        self.assertEqual(summary["wins"], 2)
        self.assertAlmostEqual(summary["net_profit"], 0.40)
        self.assertEqual(self.repository.completed_outcomes(), (1, 0))

        personal = self.repository.recent_trades(account_id="DOT90000001")
        self.assertEqual(len(personal), 1)
        self.assertEqual(personal[0]["contract_id"], "12345")
        self.assertAlmostEqual(personal[0]["app_markup_amount"], 0.02)
        self.assertEqual(personal[0]["duration_label"], "1 tick")
        self.assertEqual(personal[0]["provider_lifecycle_seconds"], 1.0)
        self.assertEqual(personal[0]["settlement_sla_seconds"], 2.0)
        self.assertEqual(personal[0]["settlement_sla_status"], "MET")
        self.assertEqual(
            self.repository.recent_trades(account_id="DOT90000999"),
            [],
        )
        markup = self.repository.markup_summary(account_id="DOT90000001")
        self.assertEqual(markup["contract_count"], 1)
        self.assertEqual(markup["confirmed_contract_count"], 1)
        self.assertEqual(markup["unconfirmed_contract_count"], 0)
        self.assertEqual(markup["status"], "CONFIRMED")
        self.assertAlmostEqual(markup["app_markup_total"], 0.02)


class BotSignalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_schedules_only_the_exact_bin_22001_rising_pattern(self) -> None:
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
            for sequence, quote in enumerate(
                [100.01, 100.01, 100.01, 100.01, 100.01], start=1
            ):
                await bot._on_tick(live_tick_payload(sequence, quote))
            self.assertEqual(spawned, [])

            for sequence, quote in enumerate(
                [100.06, 100.08, 101.00, 102.02, 103.04], start=6
            ):
                await bot._on_tick(live_tick_payload(sequence, quote))
            self.assertEqual(len(spawned), 1)
            self.assertIn("purchase_", spawned[0])
            bot.database.engine.dispose()
            for handler in list(bot.logger.handlers):
                handler.close()
            bot.logger.handlers.clear()

    async def test_all_markets_subscribe_and_keep_tick_windows_isolated(self) -> None:
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
            try:
                fake_ws = AsyncMock()
                bot.public_client.ws = fake_ws
                await bot.public_client._subscribe_ticks()
                subscriptions = [
                    json.loads(call.args[0])["ticks"]
                    for call in fake_ws.send.await_args_list
                ]
                self.assertEqual(subscriptions, list(TEST2_SYMBOLS))

                bot.connection_session_id = "connection-1"
                bot.public_client.is_connected = True
                bot.repository.set_status("RUNNING")
                bot._render_live_ticks = lambda note="": None
                spawned: list[str] = []

                def capture_task(coroutine, *, name: str) -> None:
                    spawned.append(name)
                    coroutine.close()

                bot._spawn_background_task = capture_task
                for sequence, quote in enumerate(
                    [100.06, 100.08, 101.00, 102.02, 103.04],
                    start=1,
                ):
                    await bot._on_tick(
                        live_tick_payload(sequence, quote, symbol="R_10")
                    )

                self.assertEqual(len(spawned), 1)
                self.assertEqual(len(bot.market_states["R_10"].ticks_history), 5)
                self.assertEqual(len(bot.market_states["1HZ100V"].ticks_history), 0)
                self.assertEqual(
                    bot.repository.recent_signals(1)[0]["symbol"],
                    "R_10",
                )
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()

    async def test_cooldown_blocked_match_is_recorded_with_skip_reason(self) -> None:
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
            try:
                bot.connection_session_id = "connection-1"
                bot.public_client.is_connected = True
                bot.repository.set_status("RUNNING")
                bot._render_live_ticks = lambda note="": None
                bot.cooldown.restore(
                    ticks_remaining=1,
                    consecutive_wins=0,
                    consecutive_losses=1,
                )
                bot.tick_sequence = 4
                bot.ticks_history.extend(
                    [
                        {"quote": 101.06, "display": "101.06", "last_digit": "6", "epoch": 1_700_000_001, "tick_id": "tick-1"},
                        {"quote": 101.08, "display": "101.08", "last_digit": "8", "epoch": 1_700_000_002, "tick_id": "tick-2"},
                        {"quote": 102.00, "display": "102.00", "last_digit": "0", "epoch": 1_700_000_003, "tick_id": "tick-3"},
                        {"quote": 103.02, "display": "103.02", "last_digit": "2", "epoch": 1_700_000_004, "tick_id": "tick-4"},
                    ]
                )
                bot.raw_tick_digits.extend([6, 8, 0, 2])

                await bot._on_tick(live_tick_payload(5, 104.04))

                recent = bot.repository.recent_signals(1)
                self.assertEqual(len(recent), 1)
                self.assertEqual(recent[0]["final_status"], "SKIP_COOLDOWN")
                self.assertEqual(recent[0]["trigger_digits"], [6, 8, 0, 2, 4])
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()

    async def test_raw_match_recovery_creates_candidate_when_detector_returns_none(self) -> None:
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
            try:
                bot.connection_session_id = "connection-1"
                bot.public_client.is_connected = True
                bot.repository.set_status("RUNNING")
                bot._render_live_ticks = lambda note="": None
                spawned: list[str] = []

                def capture_task(coroutine, *, name: str) -> None:
                    spawned.append(name)
                    coroutine.close()

                bot._spawn_background_task = capture_task
                bot.signal_detector.observe = lambda *args, **kwargs: None

                for sequence, quote in enumerate(
                    [100.06, 100.08, 101.00, 102.02, 103.04], start=1
                ):
                    await bot._on_tick(live_tick_payload(sequence, quote))

                self.assertEqual(len(spawned), 1)
                recent = bot.repository.recent_signals(1)
                self.assertEqual(recent[0]["trigger_digits"], [6, 8, 0, 2, 4])
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()

    async def test_non_rising_match_is_skipped(self) -> None:
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
            try:
                bot.connection_session_id = "connection-1"
                bot.public_client.is_connected = True
                bot.repository.set_status("RUNNING")
                bot._render_live_ticks = lambda note="": None
                spawned: list[str] = []

                def capture_task(coroutine, *, name: str) -> None:
                    spawned.append(name)
                    coroutine.close()

                bot._spawn_background_task = capture_task

                for sequence, quote in enumerate(
                    [105.06, 104.08, 103.00, 102.02, 101.04], start=1
                ):
                    await bot._on_tick(live_tick_payload(sequence, quote))

                self.assertEqual(spawned, [])
                recent = bot.repository.recent_signals(1)
                self.assertEqual(recent[0]["final_status"], "SKIP_NOT_RISING")
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()

    async def test_soft_rising_momentum_allows_controlled_pullback(self) -> None:
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
            try:
                bot.connection_session_id = "connection-1"
                bot.public_client.is_connected = True
                bot.repository.set_status("RUNNING")
                bot._render_live_ticks = lambda note="": None
                spawned: list[str] = []

                def capture_task(coroutine, *, name: str) -> None:
                    spawned.append(name)
                    coroutine.close()

                bot._spawn_background_task = capture_task

                for sequence, quote in enumerate(
                    [100.06, 100.18, 100.30, 100.22, 100.34],
                    start=1,
                ):
                    await bot._on_tick(live_tick_payload(sequence, quote))

                self.assertEqual(len(spawned), 1)
                self.assertIn("purchase_", spawned[0])
                self.assertFalse(bot._last_three_ticks_rising())
                self.assertTrue(bot._soft_rising_momentum())

                bot.ticks_history.clear()
                for quote in [105.00, 104.00, 103.00, 102.00]:
                    bot.ticks_history.append({"quote": quote})
                self.assertFalse(bot._high_frequency_momentum())

                bot.ticks_history.clear()
                for quote in [100.50, 100.10, 100.20, 100.15]:
                    bot.ticks_history.append({"quote": quote})
                self.assertTrue(bot._high_frequency_momentum())
            finally:
                bot.database.engine.dispose()
                for handler in list(bot.logger.handlers):
                    handler.close()
                bot.logger.handlers.clear()


if __name__ == "__main__":
    unittest.main()
