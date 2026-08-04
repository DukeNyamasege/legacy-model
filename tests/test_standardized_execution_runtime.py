from __future__ import annotations

import logging
import time
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import app.aidr_loss_continuation_fix as continuation
from app.guaranteed_signal_delivery import (
    IMMEDIATE_SIGNAL_MAX_AGE_SECONDS,
    _ImmediateTickSequence,
    _finalize_private_boundary,
    _role_matches_account,
    refresh_signal_for_delivery,
)
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
        signal.generated_monotonic = time.monotonic()
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

    def test_recent_signal_tracks_ticks_only_during_short_purchase_window(self) -> None:
        install_standardized_signal_metadata()
        signal = _signal(signal_id="recent-qualified")
        signal.generated_monotonic = time.monotonic()
        signal._standardized_cycle_id = "cycle-immediate"
        market = SimpleNamespace(
            tick_sequence=51,
            ticks_history=[{"quote": Decimal("202.45"), "epoch": 900}],
        )
        bot = SimpleNamespace(
            market_states={"1HZ100V": market},
            _tick_identity=lambda symbol, epoch, quote: f"{symbol}:{epoch}:{quote}",
            logger=logging.getLogger("immediate-signal-test"),
        )

        self.assertTrue(refresh_signal_for_delivery(bot, signal))
        self.assertIsInstance(signal.tick_sequence, _ImmediateTickSequence)
        self.assertEqual(int(signal.tick_sequence), 51)

        market.tick_sequence = 52
        market.ticks_history.append(
            {"quote": Decimal("202.46"), "epoch": 901}
        )
        self.assertEqual(int(signal.tick_sequence), 52)

        self.assertTrue(_finalize_private_boundary(bot, signal))
        self.assertIsInstance(signal.tick_sequence, int)
        self.assertEqual(signal.tick_sequence, 52)
        clear_standardized_cycle_id(signal)

    def test_old_signal_is_rejected_instead_of_held(self) -> None:
        install_standardized_signal_metadata()
        signal = _signal(signal_id="expired-qualified")
        signal.generated_monotonic = (
            time.monotonic() - IMMEDIATE_SIGNAL_MAX_AGE_SECONDS - 1.0
        )
        signal._standardized_cycle_id = "cycle-expired"
        market = SimpleNamespace(
            tick_sequence=60,
            ticks_history=[{"quote": Decimal("303.10"), "epoch": 1000}],
        )
        bot = SimpleNamespace(
            market_states={"1HZ100V": market},
            _tick_identity=lambda symbol, epoch, quote: f"{symbol}:{epoch}:{quote}",
            logger=logging.getLogger("expired-signal-test"),
        )
        self.assertFalse(refresh_signal_for_delivery(bot, signal))
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

        self.assertNotEqual(
            _route_key(route_a, over_one),
            _route_key(route_a, over_three),
        )
        self.assertNotEqual(
            _route_key(route_a, over_one),
            _route_key(route_b, over_one),
        )
        self.assertNotEqual(
            _queue_key(route_a, over_one),
            _queue_key(route_a, over_three),
        )

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
        self.assertEqual(
            _role_spec(continuation.FIRST_RECOVERY_ROLE),
            (3, True),
        )
        self.assertEqual(
            _role_spec(continuation.POST_VIRTUAL_ROLE),
            (4, True),
        )

    def test_live_manual_scope_keeps_exact_role_membership(self) -> None:
        selection = SimpleNamespace(family="digits", side="under")
        normal = SimpleNamespace(
            selection=selection,
            mode="NORMAL_MODE",
            split_remaining=0,
        )
        recovery = SimpleNamespace(
            selection=selection,
            mode="RECOVERY_PENDING",
            split_remaining=0,
        )
        post_virtual = SimpleNamespace(
            selection=selection,
            mode="RECOVERY_PENDING",
            split_remaining=1,
        )
        virtual = SimpleNamespace(
            selection=selection,
            mode="VIRTUAL_MODE",
            split_remaining=0,
        )
        self.assertTrue(
            _role_matches_account(
                SimpleNamespace(
                    family="digits",
                    side="under",
                    role="NORMAL",
                ),
                normal,
            )
        )
        self.assertTrue(
            _role_matches_account(
                SimpleNamespace(
                    family="digits",
                    side="under",
                    role="RECOVERY",
                ),
                recovery,
            )
        )
        self.assertTrue(
            _role_matches_account(
                SimpleNamespace(
                    family="digits",
                    side="under",
                    role="POST_VIRTUAL",
                ),
                post_virtual,
            )
        )
        self.assertTrue(
            _role_matches_account(
                SimpleNamespace(
                    family="digits",
                    side="under",
                    role="VIRTUAL",
                ),
                virtual,
            )
        )


class DeploymentSourceInvariantTests(unittest.TestCase):
    def test_worker_installs_standardization_after_transport_guard(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        concurrency = source.index("install_multi_strategy_concurrency_guard()")
        metadata = source.index("install_standardized_signal_metadata()")
        standardized = source.index("install_standardized_execution_runtime()")
        immediate = source.index("install_guaranteed_signal_delivery()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(concurrency, metadata)
        self.assertLess(metadata, standardized)
        self.assertLess(standardized, immediate)
        self.assertLess(immediate, bot)

    def test_standardized_router_removes_cross_group_winner_statuses(self) -> None:
        source = (
            ROOT / "app" / "standardized_execution_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('status="SKIP_AIDR_ROLE_FAIRNESS"', source)
        self.assertNotIn(
            'status="SKIP_MULTI_STRATEGY_ARBITRATION"',
            source,
        )
        self.assertIn("for role in AIDR_EXECUTION_ORDER", source)
        self.assertIn(
            "for selected, economics, route in selected_groups",
            source,
        )
        self.assertIn("competition_removed=true", source)
        self.assertIn("role_competition_removed=true", source)

    def test_every_scoped_account_has_receipt_or_skip_diagnostics(self) -> None:
        source = (
            ROOT / "app" / "standardized_execution_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("ACCOUNT_CYCLE_RECEIVED", source)
        self.assertIn("ACCOUNT_CYCLE_NOT_PURCHASED", source)
        self.assertIn("ACCOUNT_CYCLE_WAITING", source)
        self.assertIn("reason_code=%s", source)
        self.assertIn("global_execution_continues=true", source)
        self.assertIn('"signal_waiting"', source)
        self.assertIn('"cycle_skipped"', source)

    def test_immediate_runtime_has_no_infinite_signal_holding(self) -> None:
        source = (
            ROOT / "app" / "guaranteed_signal_delivery.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('float("inf")', source)
        self.assertNotIn("STANDARDIZED_SIGNAL_PINNED", source)
        self.assertIn("IMMEDIATE_SIGNAL_DEADLINE_MISSED", source)
        self.assertIn("IMMEDIATE_PRIVATE_BUY_BOUNDARY", source)
        self.assertIn("infinite_signal_holding=false", source)
        self.assertIn("connect_before_buy=true", source)
        self.assertIn("reconnect_retry=1", source)

    def test_one_system_trigger_dispatches_all_account_roles(self) -> None:
        source = (
            ROOT / "app" / "guaranteed_signal_delivery.py"
        ).read_text(encoding="utf-8")
        self.assertIn("AIDR_SHARED_TRIGGER_DISPATCH", source)
        self.assertIn("independent_role_edge_gate=false", source)
        self.assertIn("for role in standardized.AIDR_EXECUTION_ORDER", source)
        self.assertIn(
            "scopes[continuation.NORMAL_ROLE]",
            source,
        )
        self.assertIn(
            "scopes[continuation.FIRST_RECOVERY_ROLE]",
            source,
        )
        self.assertIn(
            "scopes[continuation.POST_VIRTUAL_ROLE]",
            source,
        )

    def test_private_connections_are_ready_before_buy_and_retry_once(self) -> None:
        source = (
            ROOT / "app" / "guaranteed_signal_delivery.py"
        ).read_text(encoding="utf-8")
        self.assertIn("wait_until_connected", source)
        self.assertIn("PRIVATE_CONNECTION_PURCHASE_TIMEOUT", source)
        self.assertIn("PRIVATE_BUY_CONNECTION_RETRY", source)
        self.assertIn("_connect_before_private_buy", source)
        self.assertIn("first_results = await purchase_batch(ready)", source)
        self.assertIn("initially_unready", source)
        self.assertIn("ready_accounts_purchase_now=true", source)
        self.assertIn("final_blocked.pop(account_id, None)", source)

    def test_private_connection_scheduler_uses_one_start_slot_per_attempt(self) -> None:
        source = (
            ROOT / "app" / "private_websocket_rate_limit.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(source.count("await gate.wait_for_start_slot()"), 1)
        self.assertIn("start_slots_per_attempt=1", source)
        self.assertIn("wake_private_connection", source)
        self.assertIn("wait_until_connected", source)
        self.assertIn("0.25", source)
        self.assertIn("handshake_concurrency", source)


if __name__ == "__main__":
    unittest.main()
