from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.per_account_virtual_runtime import (
    VIRTUAL_EXIT_AFTER_WINS,
    VIRTUAL_TRIGGER_ACTUAL_LOSSES,
    _open_virtual_accounts,
    platform_status_for,
    uniform_digit_alignment,
)
from app.repositories.rf_dir5_repository import VIRTUAL_MODE


ROOT = Path(__file__).resolve().parents[1]


class AccountIsolationInvariantTests(unittest.TestCase):
    def test_account_outcomes_never_become_platform_stop(self) -> None:
        for status in (
            "STOPPED",
            "MANUAL_PAUSE",
            "TAKE_PROFIT",
            "STOP_LOSS",
            "PURCHASE_INSUFFICIENT_BALANCE",
            "TOKEN_REQUIRED",
            "CREDENTIAL_ERROR",
        ):
            self.assertEqual(platform_status_for(status, "personal reason"), ("RUNNING", ""))

    def test_infrastructure_reconnect_remains_platform_visible(self) -> None:
        self.assertEqual(
            platform_status_for("RECONNECTING", "TRADER_LOCK_LOST"),
            ("RECONNECTING", "TRADER_LOCK_LOST"),
        )

    def test_uniform_virtual_lifecycle_constants(self) -> None:
        self.assertEqual(VIRTUAL_TRIGGER_ACTUAL_LOSSES, 2)
        self.assertEqual(VIRTUAL_EXIT_AFTER_WINS, 1)

    def test_virtual_digit_alignment_has_no_five_percent_tightening(self) -> None:
        self.assertAlmostEqual(uniform_digit_alignment("digits", "over", 2), 0.70)
        self.assertAlmostEqual(uniform_digit_alignment("digits", "under", 7), 0.70)
        self.assertAlmostEqual(uniform_digit_alignment("parity", "even"), 0.50)
        self.assertAlmostEqual(uniform_digit_alignment("parity", "odd"), 0.50)


class _Logger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple, dict]] = []

    def warning(self, *args, **kwargs) -> None:
        self.messages.append(("warning", args, kwargs))

    def info(self, *args, **kwargs) -> None:
        self.messages.append(("info", args, kwargs))

    def error(self, *args, **kwargs) -> None:
        self.messages.append(("error", args, kwargs))

    def exception(self, *args, **kwargs) -> None:
        self.messages.append(("exception", args, kwargs))


class _VirtualRepository:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def start_virtual_trade(self, **kwargs):
        managed_id = int(kwargs["managed_account_id"])
        self.calls.append(managed_id)
        if managed_id == 1:
            raise RuntimeError("one account row failed")
        return {
            "account": kwargs["account_id_masked"],
            "recovery_debt": 1.0,
        }


class _SignalRepository:
    def __init__(self) -> None:
        self.marks: list[dict] = []

    def mark_signal(self, _signal_id: str, **kwargs) -> None:
        self.marks.append(dict(kwargs))


class PerAccountVirtualFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_virtual_account_failure_does_not_block_another(self) -> None:
        routes = [
            SimpleNamespace(
                token="token-1",
                account_id="DOT100001",
                managed_id=1,
                mode=VIRTUAL_MODE,
            ),
            SimpleNamespace(
                token="token-2",
                account_id="DOT100002",
                managed_id=2,
                mode=VIRTUAL_MODE,
            ),
        ]
        statuses: list[tuple[int, str]] = []
        bot = SimpleNamespace(
            rf_repository=_VirtualRepository(),
            repository=_SignalRepository(),
            logger=_Logger(),
            _set_account_execution_status=lambda managed_id, status, _reason: statuses.append(
                (int(managed_id), str(status))
            ),
        )
        signal = SimpleNamespace(
            signal_id="signal-1",
            symbol="1HZ100V",
            contract_type="DIGITEVEN",
            barrier="",
        )

        with (
            patch(
                "app.per_account_virtual_runtime._virtual_routes",
                return_value=routes,
            ),
            patch("app.per_account_virtual_runtime._ensure_parent"),
            patch(
                "app.multi_strategy_runtime._configured_stake",
                return_value=0.50,
            ),
        ):
            await _open_virtual_accounts(
                bot,
                signal,
                {1, 2},
                family="parity",
                side="even",
            )

        self.assertEqual(bot.rf_repository.calls, [1, 2])
        self.assertIn((1, "virtual_retry"), statuses)
        self.assertIn((2, "virtual_protection"), statuses)
        self.assertTrue(bot.repository.marks)


class DeploymentSourceInvariantTests(unittest.TestCase):
    def test_worker_install_order_and_os_only_shutdown(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        isolation = source.index("install_account_isolation_invariants()")
        strategy = source.index("install_strategy_v2_runtime()")
        uniform = source.index("install_uniform_virtual_runtime()")
        purchase_guard = source.index("install_multi_strategy_concurrency_guard()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(isolation, bot)
        self.assertLess(strategy, uniform)
        self.assertLess(uniform, purchase_guard)
        self.assertLess(purchase_guard, bot)
        self.assertEqual(source.count("bot.is_running = False"), 1)
        self.assertIn("Only an operating-system shutdown signal", source)

    def test_base_worker_has_no_account_driven_process_shutdown(self) -> None:
        source = (ROOT / "enhanced_bot.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("self.is_running = False"), 1)
        shutdown = source.split("self.is_running = False", 1)[0][-500:]
        self.assertIn("TRADER_LOCK_LOST", shutdown)
        repository = (
            ROOT / "app" / "repositories" / "test2_repository.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '{"EMERGENCY_STOP", "MANUAL_PAUSE", "STOPPED"}',
            repository,
        )

    def test_api_installs_account_isolation_before_repository_creation(self) -> None:
        source = (ROOT / "app" / "api_v3.py").read_text(encoding="utf-8")
        isolation = source.index("install_account_isolation_invariants()")
        api_import = source.index("from app.api_account_lifecycle import app")
        self.assertLess(isolation, api_import)

    def test_virtual_runtime_bypasses_financial_gates_only_for_zero_cost_observation(self) -> None:
        source = (ROOT / "app" / "per_account_virtual_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("expected_payout=None", source)
        self.assertIn("global_execution_continues=true", source)
        self.assertIn('kwargs["virtual_trigger_actual_losses"]', source)
        self.assertIn('kwargs["exit_after_wins"]', source)
        self.assertIn('kwargs["max_observations"] = 0', source)
        self.assertNotIn("bot.is_trading_locked", source)
        self.assertNotIn("pending_contracts_for_current_cycle", source)

    def test_strategy_contract_and_soft_gate_have_no_virtual_tightening(self) -> None:
        contract = json.loads(
            (ROOT / "app" / "aidr_strategy_contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(contract["execution"]["virtual_trigger_actual_losses"], 2)
        self.assertEqual(contract["execution"]["virtual_confirmation_wins"], 1)
        self.assertEqual(contract["quality"]["post_virtual_tightening_factor"], 1.0)
        source = (ROOT / "app" / "aidr_virtual_soft_gate.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("POST_VIRTUAL_TIGHTENING_FACTOR = 1.0", source)
        self.assertIn(
            "POST_VIRTUAL_ALIGNMENT = _ordinary_over_hit_rate",
            source,
        )


if __name__ == "__main__":
    unittest.main()
