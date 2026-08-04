from __future__ import annotations

import logging
import math
import unittest
from pathlib import Path
from types import SimpleNamespace

import app.private_buy_parameter_hardening as buy_hardening
from app.recovery_state_persistence_hardening import _persist_recovery_attempt
from app.repositories.rf_dir5_repository import (
    REAL_RECOVERY_PENDING,
    VIRTUAL_WAITING_FOR_WIN,
)
from app.settlement_observability_hardening import (
    _SettlementObservabilityFilter,
    _settled_contract_duration,
)

ROOT = Path(__file__).resolve().parents[1]


class ContractParameterHardeningTests(unittest.TestCase):
    def test_digit_contracts_are_forced_to_one_tick(self) -> None:
        cleaned = buy_hardening._clean_contract_parameters(
            {
                "contract_type": "digitover",
                "amount": "0.50",
                "barrier": 4,
                "duration": 5,
                "duration_unit": "t",
                "app_markup_percentage": 3,
            }
        )
        self.assertEqual(cleaned["contract_type"], "DIGITOVER")
        self.assertEqual(cleaned["duration"], 1)
        self.assertEqual(cleaned["duration_unit"], "t")
        self.assertEqual(cleaned["barrier"], "4")
        self.assertEqual(cleaned["amount"], 0.50)
        self.assertNotIn("app_markup_percentage", cleaned)

    def test_even_odd_are_one_tick_without_barrier(self) -> None:
        cleaned = buy_hardening._clean_contract_parameters(
            {
                "contract_type": "DIGITEVEN",
                "amount": 0.35,
                "duration": 9,
                "duration_unit": "m",
            }
        )
        self.assertEqual(cleaned["duration"], 1)
        self.assertEqual(cleaned["duration_unit"], "t")
        self.assertNotIn("barrier", cleaned)

    def test_rise_fall_duration_remains_strategy_controlled(self) -> None:
        cleaned = buy_hardening._clean_contract_parameters(
            {
                "contract_type": "CALL",
                "amount": 1.00,
                "duration": 5,
                "duration_unit": "t",
            }
        )
        self.assertEqual(cleaned["duration"], 5)
        self.assertEqual(cleaned["duration_unit"], "t")

    def test_invalid_amount_and_digit_barrier_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            buy_hardening._clean_contract_parameters(
                {
                    "contract_type": "DIGITOVER",
                    "amount": math.inf,
                    "barrier": 2,
                }
            )
        with self.assertRaises(ValueError):
            buy_hardening._clean_contract_parameters(
                {
                    "contract_type": "DIGITUNDER",
                    "amount": 0.50,
                    "barrier": 10,
                }
            )

    def test_public_proposal_matches_private_one_tick_buy(self) -> None:
        old = buy_hardening._ORIGINAL_PROPOSAL_REQUEST

        def original(_self, signal, stake_amount, duration_ticks):
            return {
                "proposal": 1,
                "contract_type": signal.contract_type,
                "amount": stake_amount,
                "duration": duration_ticks,
                "duration_unit": "t",
            }

        try:
            buy_hardening._ORIGINAL_PROPOSAL_REQUEST = original
            signal = SimpleNamespace(contract_type="DIGITOVER", duration_ticks=5)
            request = buy_hardening._one_tick_proposal_request(
                object(), signal, 0.50, 5
            )
        finally:
            buy_hardening._ORIGINAL_PROPOSAL_REQUEST = old

        self.assertEqual(request["duration"], 1)
        self.assertEqual(request["duration_unit"], "t")
        self.assertEqual(signal.duration_ticks, 1)


class SettlementObservabilityHardeningTests(unittest.TestCase):
    def test_digit_settlement_reports_one_tick(self) -> None:
        bot = SimpleNamespace(duration=5)
        self.assertEqual(
            _settled_contract_duration(
                bot,
                {"contract_type": "DIGITOVER", "duration": 5},
            ),
            1,
        )

    def test_rise_fall_settlement_uses_provider_duration(self) -> None:
        bot = SimpleNamespace(duration=5)
        self.assertEqual(
            _settled_contract_duration(
                bot,
                {"contract_type": "CALL", "duration": 3},
            ),
            3,
        )

    def test_contract_timing_filter_uses_exact_contract_override(self) -> None:
        bot = SimpleNamespace(
            duration=5,
            _settlement_duration_log_overrides={"123": 1},
        )
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=(
                "CONTRACT_TIMING account=%s contract_id=%s duration=%s_ticks "
                "lifecycle_seconds=%.3f provider_lifecycle_seconds=%s "
                "sla_seconds=%.1f sla_status=%s"
            ),
            args=("DOT***123", 123, 5, 1.0, "1.000", 15.0, "MET"),
            exc_info=None,
        )
        self.assertTrue(_SettlementObservabilityFilter(bot).filter(record))
        self.assertEqual(record.args[2], 1)

    def test_unavailable_markup_is_informational_not_execution_error(self) -> None:
        bot = SimpleNamespace(duration=1)
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg=(
                "APP_MARKUP_NOT_CONFIRMED account=%s contract_id=%s "
                "expected_percentage=%.2f reported_app_markup_amount=%s; "
                "verify Registered Apps markup"
            ),
            args=("DOT***123", 123, 3.0, "unavailable"),
            exc_info=None,
        )
        self.assertTrue(_SettlementObservabilityFilter(bot).filter(record))
        self.assertIn("APP_MARKUP_UNVERIFIED", record.msg)
        self.assertNotIn("APP_MARKUP_NOT_CONFIRMED", record.msg)
        self.assertEqual(record.levelno, logging.INFO)
        self.assertEqual(record.levelname, "INFO")


class _FakeSession:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, _model, _managed_id, **_kwargs):
        return self.state


class _FakeDatabase:
    def __init__(self, state):
        self.state = state

    def session(self):
        return _FakeSession(self.state)


class _FakeRepository:
    def __init__(self, state):
        self.database = _FakeDatabase(state)


class RecoveryPersistenceHardeningTests(unittest.TestCase):
    def _state(self, **overrides):
        values = {
            "protection_mode": REAL_RECOVERY_PENDING,
            "recovery_loss_debt": 2.0,
            "recovery_attempt_active": False,
            "recovery_pending": True,
            "recovery_pending_since": None,
            "updated_at": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_pending_recovery_is_persisted(self) -> None:
        state = self._state()
        self.assertTrue(_persist_recovery_attempt(_FakeRepository(state), 7))
        self.assertTrue(state.recovery_attempt_active)
        self.assertFalse(state.recovery_pending)
        self.assertEqual(state.protection_mode, REAL_RECOVERY_PENDING)
        self.assertIsNotNone(state.updated_at)

    def test_already_active_recovery_is_idempotent_success(self) -> None:
        state = self._state(
            recovery_attempt_active=True,
            recovery_pending=True,
        )
        self.assertTrue(_persist_recovery_attempt(_FakeRepository(state), 7))
        self.assertTrue(state.recovery_attempt_active)
        self.assertFalse(state.recovery_pending)

    def test_virtual_or_debt_free_account_cannot_start_real_recovery(self) -> None:
        virtual = self._state(protection_mode=VIRTUAL_WAITING_FOR_WIN)
        debt_free = self._state(recovery_loss_debt=0.0)
        self.assertFalse(_persist_recovery_attempt(_FakeRepository(virtual), 7))
        self.assertFalse(_persist_recovery_attempt(_FakeRepository(debt_free), 7))


class DeploymentSourceContractTests(unittest.TestCase):
    def test_worker_install_order(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        strict = source.index("install_aidr_strict_recovery_guard()")
        persistence = source.index(
            "install_recovery_state_persistence_hardening()"
        )
        production = source.index("install_production_worker_integration()")
        observability = source.index(
            "install_settlement_observability_hardening()"
        )
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(strict, persistence)
        self.assertLess(persistence, production)
        self.assertLess(production, observability)
        self.assertLess(observability, bot)

    def test_postgres_and_diagnostics_are_bounded(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        diagnostic = (
            ROOT / "scripts" / "diagnose_vps_performance.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("idle_in_transaction_session_timeout", compose)
        self.assertIn("max_wal_size", compose)
        self.assertIn("translate(", diagnostic)
        self.assertIn("chr(10) || chr(13) || chr(9)", diagnostic)
        self.assertNotIn("E$$[\\n\\r\\t]+$$", diagnostic)
        self.assertIn("docker compose -f docker-compose.yml", diagnostic)
        self.assertNotIn("docker-compose.vps.yml", diagnostic)


if __name__ == "__main__":
    unittest.main()
