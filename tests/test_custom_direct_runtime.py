from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.account_execution_session import (
    AccountExecutionPreparationError,
    AccountExecutionSession,
)
from app.custom_strategy_direct_runtime import _refresh_direct_accounts
from app.custom_strategy_runtime_api import _runtime_state
from app.custom_strategy_settlement import custom_virtual_outcome
from app.strategy.decision_engine import ProposalEconomics


ROOT = Path(__file__).resolve().parents[1]


class _PrivateSession:
    def __init__(self, account_id: str = "CR123") -> None:
        self.account_id = account_id
        self.is_connected = True
        self.pending_contracts: set[int] = set()
        self.requests: list[dict] = []
        self.subscriptions: list[int] = []

    async def send_request(self, payload: dict) -> dict:
        self.requests.append(dict(payload))
        return {
            "buy": {
                "contract_id": 991,
                "transaction_id": 551,
                "buy_price": 0.50,
                "payout": 0.95,
                "purchase_time": 1_786_000_000,
                "start_time": 1_786_000_000,
            }
        }

    async def subscribe_contract(self, contract_id: int) -> None:
        self.subscriptions.append(int(contract_id))


class _PublicClient:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def send_request(self, payload: dict) -> dict:
        self.requests.append(dict(payload))
        return {
            "proposal": {
                "id": "proposal-exact-account-1",
                "ask_price": 0.50,
                "payout": 0.95,
            }
        }


class _Repository:
    def __init__(self) -> None:
        self.disabled: list[tuple[int, bool]] = []
        self.purchases: list[dict] = []

    def update_managed_account(self, managed_id: int, **updates) -> None:
        self.disabled.append((int(managed_id), bool(updates.get("enabled"))))

    def register_purchase(self, **payload) -> None:
        self.purchases.append(dict(payload))


class _Bot:
    def __init__(self, *, sync_creates_state: bool = True) -> None:
        self.runtime_key = "fingerprint:CR123"
        self.valid_clients = [(self.runtime_key, "CR123")]
        self.user_profiles = {
            self.runtime_key: {
                "managed_account_id": 7,
                "account_id": "CR123",
                "api_token": "actual-trade-token",
                "martingale_enabled": True,
            }
        }
        self.clients: dict[str, dict] = {}
        self.sessions = {self.runtime_key: _PrivateSession()}
        self.market_states = {"1HZ100V": object()}
        self.symbols = ["1HZ100V"]
        self.symbol = "1HZ100V"
        self.repository = _Repository()
        self.public_client = _PublicClient()
        self.currency = "USD"
        self.app_markup_percentage = 0.0
        self.sync_creates_state = sync_creates_state
        self.sync_calls = 0
        self.statuses: list[tuple[int, str, str]] = []
        self.logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )

    def _sync_clients_with_runtime_accounts(self) -> None:
        self.sync_calls += 1
        if self.sync_creates_state:
            self.clients[self.runtime_key] = {
                "managed_account_id": 7,
                "base_stake": 0.50,
            }

    def _credential_for_token(self, token: str) -> str:
        return str(self.user_profiles.get(token, {}).get("api_token") or "")

    def _client_state_for_token(self, token: str, *, account_id: str | None = None):
        if token not in self.clients:
            raise KeyError(token)
        return self.clients[token]

    def _managed_account_id_for_token(self, token: str) -> int | None:
        raw = self.user_profiles.get(token, {}).get("managed_account_id")
        return int(raw) if raw is not None else None

    def _set_account_execution_status(self, managed_id: int, status: str, reason: str) -> None:
        self.statuses.append((int(managed_id), str(status), str(reason)))


CUSTOM_CONFIG = {
    "configured": True,
    "market_mode": "single",
    "markets": ["1HZ100V"],
    "trade_type": "over",
    "prediction": 3,
    "duration_ticks": 1,
    "conditions": [{"type": "digit_compare", "window": 1, "comparator": ">", "value": 0}],
    "virtual_hook_enabled": True,
}


class CustomRuntimeRegistrationRegressionTests(TestCase):
    def test_late_started_account_syncs_client_state_before_becoming_runnable(self) -> None:
        """Regression for current production composite-key KeyError."""
        bot = _Bot(sync_creates_state=True)
        self.assertEqual(bot.clients, {})
        with patch(
            "app.custom_strategy_direct_runtime._load_configs_for_ids",
            return_value={7: CUSTOM_CONFIG},
        ):
            runtime = _refresh_direct_accounts(
                bot,
                require_connected=True,
                fail_invalid=True,
            )
        self.assertEqual(bot.sync_calls, 1)
        self.assertIn(bot.runtime_key, bot.clients)
        self.assertIn(7, runtime)
        state, private = runtime[7].execution.prepare()
        self.assertEqual(state["managed_account_id"], 7)
        self.assertIs(private, bot.sessions[bot.runtime_key])

    def test_missing_client_state_fails_closed_before_scanner_can_use_account(self) -> None:
        bot = _Bot(sync_creates_state=False)
        with patch(
            "app.custom_strategy_direct_runtime._load_configs_for_ids",
            return_value={7: CUSTOM_CONFIG},
        ):
            runtime = _refresh_direct_accounts(
                bot,
                require_connected=True,
                fail_invalid=True,
            )
        self.assertEqual(runtime, {})
        self.assertEqual(bot.repository.disabled, [(7, False)])
        self.assertEqual(bot.valid_clients, [])
        self.assertTrue(bot.statuses)
        self.assertEqual(bot.statuses[-1][1], "error")
        self.assertIn("execution state is not registered", bot.statuses[-1][2])
        self.assertEqual(bot.sessions[bot.runtime_key].requests, [])

    def test_direct_account_session_uses_exact_proposal_id_for_exact_private_account(self) -> None:
        bot = _Bot(sync_creates_state=True)
        bot._sync_clients_with_runtime_accounts()
        execution = AccountExecutionSession(
            bot=bot,
            token=bot.runtime_key,
            account_id="CR123",
            managed_account_id=7,
        )
        signal = SimpleNamespace(
            contract_type="DIGITOVER",
            barrier="3",
            duration_ticks=1,
            symbol="1HZ100V",
        )

        economics = asyncio.run(
            execution.proposal(signal, stake=0.50, predicted_probability=0.60)
        )
        buy = asyncio.run(execution.buy_proposal(economics))

        self.assertEqual(economics.proposal_id, "proposal-exact-account-1")
        self.assertEqual(
            bot.public_client.requests[0],
            {
                "proposal": 1,
                "amount": 0.5,
                "basis": "stake",
                "contract_type": "DIGITOVER",
                "currency": "USD",
                "duration": 1,
                "duration_unit": "t",
                "underlying_symbol": "1HZ100V",
                "barrier": "3",
            },
        )
        self.assertEqual(
            bot.sessions[bot.runtime_key].requests[0],
            {"buy": "proposal-exact-account-1", "price": 0.5},
        )
        self.assertEqual(int(buy["contract_id"]), 991)

    def test_unregistered_state_never_reaches_proposal_or_buy(self) -> None:
        bot = _Bot(sync_creates_state=False)
        execution = AccountExecutionSession(
            bot=bot,
            token=bot.runtime_key,
            account_id="CR123",
            managed_account_id=7,
        )
        with self.assertRaises(AccountExecutionPreparationError):
            execution.prepare()
        self.assertEqual(bot.public_client.requests, [])
        self.assertEqual(bot.sessions[bot.runtime_key].requests, [])


class DirectPurchasePersistenceTests(TestCase):
    def test_confirmed_purchase_is_registered_to_exact_managed_account(self) -> None:
        bot = _Bot(sync_creates_state=True)
        bot._sync_clients_with_runtime_accounts()
        bot.pending_contracts_for_current_cycle = set()
        bot.contract_signal_ids = {}
        bot.contract_symbols = {}
        bot.pending_by_signal = {}
        bot.outcomes_by_signal = {}
        bot.signal_symbols = {}
        bot.pending_contract_started_at = {}
        bot.unregistered_contracts = set()
        bot._on_account_contract_registered = lambda *args, **kwargs: None

        async def no_timeout(*args, **kwargs):
            return None

        bot._cycle_timeout_watchdog = no_timeout
        execution = AccountExecutionSession(
            bot=bot,
            token=bot.runtime_key,
            account_id="CR123",
            managed_account_id=7,
        )
        signal = SimpleNamespace(
            signal_id="custom-regression-1",
            contract_type="DIGITOVER",
            barrier="3",
            duration_ticks=1,
            symbol="1HZ100V",
        )
        purchase_requested_at = datetime.now(timezone.utc)
        contract_id = asyncio.run(
            execution.register_purchase(
                signal=signal,
                buy={
                    "contract_id": 991,
                    "transaction_id": 551,
                    "buy_price": 0.50,
                    "payout": 0.95,
                    "purchase_time": 1_786_000_000,
                    "start_time": 1_786_000_000,
                },
                stake=0.50,
                profit_ratio=0.90,
                purchase_requested_at=purchase_requested_at,
            )
        )
        self.assertEqual(contract_id, 991)
        self.assertEqual(len(bot.repository.purchases), 1)
        persisted = bot.repository.purchases[0]
        self.assertEqual(persisted["managed_account_id"], 7)
        self.assertEqual(persisted["account_id"], "CR123")
        self.assertEqual(persisted["contract_duration"], 1)
        self.assertIn(991, bot.sessions[bot.runtime_key].pending_contracts)
        self.assertEqual(bot.sessions[bot.runtime_key].subscriptions, [991])


class CustomVirtualSettlementTests(TestCase):
    def test_digit_match_and_diff_are_supported(self) -> None:
        match, digit = custom_virtual_outcome(
            direction="MATCHES",
            contract_type="DIGITMATCH",
            barrier="7",
            prediction_digit=7,
            entry_quote=Decimal("100.00"),
            exit_quote=Decimal("100.07"),
            exit_digit=7,
        )
        differs, _ = custom_virtual_outcome(
            direction="DIFFERS",
            contract_type="DIGITDIFF",
            barrier="7",
            prediction_digit=7,
            entry_quote=Decimal("100.00"),
            exit_quote=Decimal("100.08"),
            exit_digit=8,
        )
        self.assertEqual((match, digit), ("WIN", 7))
        self.assertEqual(differs, "WIN")

    def test_even_odd_over_under_and_rise_fall_are_supported(self) -> None:
        cases = [
            ("EVEN", "DIGITEVEN", None, 4, "WIN"),
            ("ODD", "DIGITODD", None, 5, "WIN"),
            ("OVER", "DIGITOVER", "3", 4, "WIN"),
            ("UNDER", "DIGITUNDER", "6", 5, "WIN"),
        ]
        for direction, contract, barrier, digit, expected in cases:
            outcome, _ = custom_virtual_outcome(
                direction=direction,
                contract_type=contract,
                barrier=barrier,
                prediction_digit=int(barrier) if barrier else None,
                entry_quote=Decimal("100.00"),
                exit_quote=Decimal(f"100.0{digit}"),
                exit_digit=digit,
            )
            self.assertEqual(outcome, expected)
        rise, _ = custom_virtual_outcome(
            direction="RISE",
            contract_type="CALL",
            barrier=None,
            prediction_digit=None,
            entry_quote=Decimal("100.00"),
            exit_quote=Decimal("100.01"),
            exit_digit=1,
        )
        fall, _ = custom_virtual_outcome(
            direction="FALL",
            contract_type="PUT",
            barrier=None,
            prediction_digit=None,
            entry_quote=Decimal("100.00"),
            exit_quote=Decimal("99.99"),
            exit_digit=9,
        )
        self.assertEqual(rise, "WIN")
        self.assertEqual(fall, "WIN")


class CustomRuntimeArchitectureTests(TestCase):
    def test_runtime_state_is_not_inferred_from_enabled_flag(self) -> None:
        self.assertEqual(_runtime_state(enabled=True, status="starting"), "STARTING")
        self.assertEqual(
            _runtime_state(enabled=True, status="waiting_for_condition"),
            "WAITING_FOR_CONDITION",
        )
        self.assertEqual(_runtime_state(enabled=True, status="executing"), "EXECUTING")
        self.assertEqual(_runtime_state(enabled=True, status="running"), "RUNNING")
        self.assertEqual(_runtime_state(enabled=False, status="error"), "ERROR")
        self.assertEqual(_runtime_state(enabled=False, status="stopped"), "STOPPED")

    def test_production_worker_has_no_legacy_strategy_purchase_router(self) -> None:
        source = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        banned = (
            "custom_strategy_runtime",
            "shared_system_strategy_clock",
            "rotating_execution_cohorts",
            "scalable_group_execution",
            "guaranteed_signal_delivery",
            "standardized_execution_runtime",
            "multi_strategy_concurrency",
            "strategy_v2_runtime",
            "multi_strategy_runtime",
            "production_worker_integration",
            "tick_persistence_buffer",
            "tick_debug_logging",
        )
        for module in banned:
            self.assertNotIn(f"from app.{module} import", source)
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("python -m app.custom_strategy_worker", compose)
        self.assertNotIn("exec python -m app.worker", compose)
        self.assertNotIn("LIVE_TICK_LOG_LINES", compose)
        self.assertNotIn("TICK_PERSIST_", compose)
