from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import app.aidr_loss_continuation_fix as continuation
from app.hybrid_digit_put import DigitSignal
from app.standardized_execution_runtime import (
    AIDR_EXECUTION_ORDER,
    _queue_key,
    _role_spec,
    _route_key,
    refresh_signal_for_execution,
)
from app.standardized_signal_metadata import (
    clear_standardized_cycle_id,
    install_standardized_signal_metadata,
    standardized_cycle_id,
)


ROOT = Path(__file__).resolve().parents[1]


def _signal(*, signal_id: str = "signal-1", barrier: str = "1") -> DigitSignal:
    return DigitSignal(
        signal_id=signal_id,
        run_id="run",
        strategy_version="test",
        symbol="1HZ100V",
        direction=f"OVER_{barrier}",
        contract_type="DIGITOVER",
        duration_ticks=1,
        reference_entry_quote=Decimal("100.11"),
        quality_score=7,
        signal_tick_epoch=100,
        signal_tick_id="old-tick",
        generated_at="2026-08-04T00:00:00+00:00",
        generated_monotonic=0.0,
        connection_session_id="connection",
        tick_sequence=10,
        barrier=barrier,
        trigger_name="TEST",
        trigger_digits=(1, 2, 3),
        signal_last_digit=3,
        p100=0.70,
        p500=0.70,
        p1000=0.70,
        lower95=0.65,
        weighted_probability=0.70,
    )


class SignalMetadataCompatibilityTests(unittest.TestCase):
    def test_slotted_digit_signal_accepts_transient_cycle_id(self) -> None:
        install_standardized_signal_metadata()
        signal = _signal()
        signal._standardized_cycle_id = "cycle-123"
        self.assertEqual(signal._standardized_cycle_id, "cycle-123")
        self.assertEqual(standardized_cycle_id(signal), "cycle-123")
        clear_standardized_cycle_id(signal)
        self.assertEqual(signal._standardized_cycle_id, "")

    def test_standardized_signal_refreshes_to_current_tick(self) -> None:
        install_standardized_signal_metadata()
        signal = _signal()
        signal.generated_monotonic = __import__("time").monotonic()
        signal._standardized_cycle_id = "cycle-refresh"
        market = SimpleNamespace(
            tick_sequence=44,
            ticks_history=[{"quote": Decimal("101.27"), "epoch": 777}],
        )
        bot = SimpleNamespace(
            market_states={"1HZ100V": market},
            _tick_identity=lambda symbol, epoch, quote: f"{symbol}:{epoch}:{quote}",
        )
        self.assertTrue(refresh_signal_for_execution(bot, signal))
        self.assertEqual(signal.tick_sequence, 44)
        self.assertEqual(signal.reference_entry_quote, Decimal("101.27"))
        self.assertEqual(signal.signal_tick_epoch, 777)
        clear_standardized_cycle_id(signal)


class AccountGroupIdentityTests(unittest.TestCase):
    def test_exact_contract_and_scope_are_part_of_route_identity(self) -> None:
        route_a = SimpleNamespace(
            family="digits",
            side="over",
            role="SHARED",
            scope_ids={10, 11},
        )
        route_b = SimpleNamespace(
            family="digits",
            side="over",
            role="SHARED",
            scope_ids={12},
        )
        over_one = _signal(signal_id="one", barrier="1")
        over_three = _signal(signal_id="three", barrier="3")

        self.assertNotEqual(_route_key(route_a, over_one), _route_key(route_a, over_three))
        self.assertNotEqual(_route_key(route_a, over_one), _route_key(route_b, over_one))
        self.assertNotEqual(_queue_key(route_a, over_one), _queue_key(route_a, over_three))

    def test_system_execution_order_contains_every_role(self) -> None:
        self.assertEqual(
            AIDR_EXECUTION_ORDER,
            (
                continuation.NORMAL_ROLE,
                continuation.FIRST_RECOVERY_ROLE,
                continuation.POST_VIRTUAL_ROLE,
            ),
        )
        self.assertEqual(_role_spec(continuation.NORMAL_ROLE), (1, False))
        self.assertEqual(_role_spec(continuation.FIRST_RECOVERY_ROLE), (3, True))
        self.assertEqual(_role_spec(continuation.POST_VIRTUAL_ROLE), (4, True))


class DeploymentSourceInvariantTests(unittest.TestCase):
    def test_worker_installs_standardization_after_transport_guard(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        concurrency = source.index("install_multi_strategy_concurrency_guard()")
        metadata = source.index("install_standardized_signal_metadata()")
        standardized = source.index("install_standardized_execution_runtime()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(concurrency, metadata)
        self.assertLess(metadata, standardized)
        self.assertLess(standardized, bot)

    def test_standardized_router_removes_cross_group_winner_statuses(self) -> None:
        source = (ROOT / "app" / "standardized_execution_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('status="SKIP_AIDR_ROLE_FAIRNESS"', source)
        self.assertNotIn('status="SKIP_MULTI_STRATEGY_ARBITRATION"', source)
        self.assertIn("for role in AIDR_EXECUTION_ORDER", source)
        self.assertIn("for selected, economics, route in selected_groups", source)
        self.assertIn("competition_removed=true", source)
        self.assertIn("role_competition_removed=true", source)

    def test_every_scoped_account_has_receipt_or_skip_diagnostics(self) -> None:
        source = (ROOT / "app" / "standardized_execution_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ACCOUNT_CYCLE_RECEIVED", source)
        self.assertIn("ACCOUNT_CYCLE_NOT_PURCHASED", source)
        self.assertIn("ACCOUNT_CYCLE_WAITING", source)
        self.assertIn("reason_code=%s", source)
        self.assertIn("global_execution_continues=true", source)
        self.assertIn('"signal_waiting"', source)
        self.assertIn('"cycle_skipped"', source)

    def test_standardized_groups_can_coexist_with_disjoint_open_contracts(self) -> None:
        source = (ROOT / "app" / "multi_strategy_concurrency.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("if pending_count and not standardized", source)
        self.assertIn("if pending_count and standardized", source)
        self.assertIn("STANDARDIZED_GROUP_COEXISTS_WITH_OPEN_CONTRACTS", source)
        self.assertIn("account_scoped_eligibility=true", source)
        self.assertIn("refresh_signal_for_execution", source)

    def test_signal_metadata_supports_all_slotted_strategy_signals(self) -> None:
        source = (ROOT / "app" / "standardized_signal_metadata.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_install_for(DigitSignal)", source)
        self.assertIn("_install_for(SignalEvent)", source)
        self.assertIn('name == "_standardized_cycle_id"', source)
        self.assertIn("_METADATA_TTL_SECONDS", source)


if __name__ == "__main__":
    unittest.main()
