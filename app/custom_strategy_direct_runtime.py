from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.account_execution_session import (
    AccountExecutionError,
    AccountExecutionPreparationError,
    AccountExecutionSession,
)
from app.custom_strategy_v1 import (
    MAX_WINDOW,
    PREFERENCE_PREFIX,
    SUPPORTED_MARKETS,
    build_custom_signal,
    custom_strategy_fingerprint,
    evaluate_custom_strategy,
    market_selected,
    nominal_probability,
    normalize_custom_strategy,
)
from app.models import RuntimePreference
from app.rf_dir5_bot import RFDir5TradingBot


LOGGER = logging.getLogger(__name__)
_INSTALLED = False


@dataclass(slots=True)
class DirectRuntimeAccount:
    token: str
    account_id: str
    managed_id: int
    config: dict[str, Any]
    execution: AccountExecutionSession


def _config_key(managed_id: int) -> str:
    return f"{PREFERENCE_PREFIX}{int(managed_id)}"


def _active_identity_rows(bot: RFDir5TradingBot) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for token, account_id in list(getattr(bot, "valid_clients", []) or []):
        profile = dict(getattr(bot, "user_profiles", {}).get(token, {}) or {})
        raw_id = profile.get("managed_account_id")
        try:
            managed_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        rows.append((str(token), str(account_id), managed_id))
    return rows


def _load_configs_for_ids(
    bot: RFDir5TradingBot,
    managed_ids: set[int],
) -> dict[int, dict[str, Any]]:
    if not managed_ids:
        return {}
    key_to_id = {_config_key(value): int(value) for value in managed_ids}
    configs: dict[int, dict[str, Any]] = {}
    with bot.repository.database.session() as session:
        rows = session.scalars(
            select(RuntimePreference).where(
                RuntimePreference.preference_key.in_(list(key_to_id))
            )
        ).all()
    for row in rows:
        managed_id = key_to_id.get(str(row.preference_key or ""))
        if managed_id is None:
            continue
        try:
            config = normalize_custom_strategy(json.loads(str(row.preference_value or "")))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if bool(config.get("configured")):
            configs[managed_id] = config
    return configs


def _required_symbols(configs: list[dict[str, Any]]) -> list[str]:
    symbols: set[str] = set()
    for config in configs:
        if str(config.get("market_mode") or "all") == "all":
            symbols.update(SUPPORTED_MARKETS)
        else:
            symbols.update(str(value) for value in config.get("markets") or [])
    return [symbol for symbol in SUPPORTED_MARKETS if symbol in symbols]


def _fail_closed(
    bot: RFDir5TradingBot,
    managed_id: int,
    reason: str,
    *,
    log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
) -> None:
    safe_reason = str(reason or "account execution session could not be initialized")[:160]
    try:
        bot.repository.update_managed_account(int(managed_id), enabled=False)
    except Exception:
        bot.logger.exception(
            "CUSTOM_RUNTIME_DISABLE_FAILED managed_id=%s",
            managed_id,
        )
    bot._set_account_execution_status(int(managed_id), "error", safe_reason)
    bot.valid_clients = [
        item
        for item in list(getattr(bot, "valid_clients", []) or [])
        if bot._managed_account_id_for_token(item[0]) != int(managed_id)
    ]
    bot.logger.error(
        "%s managed_id=%s reason=%s scanning=false purchase=false",
        log_event,
        managed_id,
        safe_reason,
    )


def _refresh_direct_accounts(
    bot: RFDir5TradingBot,
    *,
    require_connected: bool,
    fail_invalid: bool,
) -> dict[int, DirectRuntimeAccount]:
    # This synchronization is the critical fix for the reproduced composite-key
    # KeyError. validate_accounts() may introduce runtime keys after bot startup;
    # the client-state map must be rebuilt before any account can become runnable.
    bot._sync_clients_with_runtime_accounts()

    identities = _active_identity_rows(bot)
    ids = {managed_id for _token, _account, managed_id in identities}
    configs = _load_configs_for_ids(bot, ids)
    runtime: dict[int, DirectRuntimeAccount] = {}
    for token, account_id, managed_id in identities:
        config = configs.get(managed_id)
        if config is None:
            if fail_invalid:
                _fail_closed(
                    bot,
                    managed_id,
                    "Trading stopped: save a valid Custom Strategy before starting Auto Trading.",
                )
            continue
        execution = AccountExecutionSession(
            bot=bot,
            token=token,
            account_id=account_id,
            managed_account_id=managed_id,
        )
        if require_connected:
            try:
                execution.prepare()
            except AccountExecutionPreparationError as exc:
                # A private session can legitimately still be connecting. Only a
                # connected-but-invalid ownership/state is fatal here; the caller
                # decides whether to keep waiting for a connection.
                private = getattr(bot, "sessions", {}).get(token)
                if private is None or not bool(getattr(private, "is_connected", False)):
                    bot._set_account_execution_status(
                        managed_id,
                        "starting",
                        "Initializing authenticated Deriv trading session",
                    )
                    continue
                if fail_invalid:
                    _fail_closed(bot, managed_id, f"Trading stopped: {exc}")
                continue
        runtime[managed_id] = DirectRuntimeAccount(
            token=token,
            account_id=account_id,
            managed_id=managed_id,
            config=config,
            execution=execution,
        )

    bot._custom_direct_accounts = runtime
    requested = _required_symbols([item.config for item in runtime.values()])
    if requested:
        unavailable = [symbol for symbol in requested if symbol not in bot.market_states]
        if unavailable:
            for managed_id in list(runtime):
                if any(
                    market_selected(runtime[managed_id].config, symbol)
                    for symbol in unavailable
                ):
                    _fail_closed(
                        bot,
                        managed_id,
                        "Trading stopped: configured market is unavailable in this worker.",
                    )
                    runtime.pop(managed_id, None)
            requested = _required_symbols([item.config for item in runtime.values()])
        if requested:
            bot.symbols = requested
            bot.symbol = requested[0]
    return runtime


def _account_has_open_actual(item: DirectRuntimeAccount) -> bool:
    private = getattr(item.execution.bot, "sessions", {}).get(item.token)
    return bool(private and getattr(private, "pending_contracts", set()))


def _account_has_open_virtual(bot: RFDir5TradingBot, managed_id: int) -> bool:
    due = getattr(bot, "_custom_direct_virtual_due", {})
    return int(managed_id) in due


def _quotes(market: Any) -> list[Any]:
    return [
        item["quote"]
        for item in list(getattr(market, "ticks_history", []) or [])
        if isinstance(item, dict) and item.get("quote") is not None
    ]


def _digits(market: Any) -> list[int]:
    result: list[int] = []
    for value in list(getattr(market, "raw_tick_digits", []) or []):
        try:
            digit = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= digit <= 9:
            result.append(digit)
    return result


async def _settle_due_virtuals(
    bot: RFDir5TradingBot,
    *,
    symbol: str,
    market: Any,
    quote: Decimal,
    epoch: int,
    digit: int | None,
) -> None:
    due: dict[int, tuple[str, int]] = getattr(bot, "_custom_direct_virtual_due", {})
    bot._custom_direct_virtual_due = due
    relevant = {
        managed_id: exit_sequence
        for managed_id, (due_symbol, exit_sequence) in due.items()
        if due_symbol == symbol and int(exit_sequence) <= int(market.tick_sequence)
    }
    if not relevant:
        return
    settled = list(
        bot.rf_repository.settle_due_virtual_trades(
            symbol=symbol,
            tick_sequence=int(market.tick_sequence),
            exit_quote=quote,
            exit_epoch=epoch,
            exit_digit=digit,
            exit_after_wins=1,
            max_observations=0,
        )
        or []
    )
    for managed_id in relevant:
        due.pop(managed_id, None)
        account = getattr(bot, "_custom_direct_accounts", {}).get(managed_id)
        if account is not None:
            bot._set_account_execution_status(
                managed_id,
                "waiting_for_condition",
                "Virtual observation settled; waiting for the next qualifying pattern",
            )
    for payload in settled:
        bot.logger.info(
            "CUSTOM_VIRTUAL_TRADE_SETTLED account=%s market=%s result=%s "
            "actual_financial_impact=0",
            payload.get("account", "account"),
            payload.get("market", symbol),
            payload.get("result", "unknown"),
        )
    if settled:
        try:
            await bot._notify_dashboard_settlement()
        except Exception:
            bot.logger.exception("CUSTOM_VIRTUAL_DASHBOARD_NOTIFY_FAILED")


async def _execute_for_account(
    bot: RFDir5TradingBot,
    item: DirectRuntimeAccount,
    *,
    signal: Any,
) -> None:
    managed_id = int(item.managed_id)
    inflight: set[int] = getattr(bot, "_custom_direct_inflight", set())
    try:
        item.execution.prepare()
        bot._set_account_execution_status(
            managed_id,
            "executing",
            "Custom Strategy qualified; preparing exact account execution",
        )
        bot.logger.info(
            "CUSTOM_STRATEGY_SIGNAL_QUALIFIED signal_id=%s managed_id=%s symbol=%s "
            "trade_type=%s contract_type=%s barrier=%s duration_ticks=%s conditions=%s "
            "entry_gate=user_custom_pattern condition_join=AND",
            signal.signal_id,
            managed_id,
            signal.symbol,
            item.config.get("trade_type"),
            signal.contract_type,
            getattr(signal, "barrier", "") or "-",
            max(1, int(getattr(signal, "duration_ticks", 1) or 1)),
            len(item.config.get("conditions") or []),
        )
        protection = bot.rf_repository.virtual_protection_for_account(
            managed_account_id=managed_id,
            account_id_masked="",
        )
        if (
            bool(item.config.get("virtual_hook_enabled", True))
            and str(protection.get("mode") or "") == "VIRTUAL_MODE"
        ):
            state = item.execution.prepare()[0]
            virtual = bot.rf_repository.start_virtual_trade(
                managed_account_id=managed_id,
                account_id_masked=str(protection.get("account") or ""),
                signal=signal,
                configured_stake=float(state.get("base_stake") or 0.50),
                simulated_stake=float(state.get("base_stake") or 0.50),
                expected_payout=None,
            )
            if virtual is None:
                raise AccountExecutionError(
                    "virtual protection could not open its account observation"
                )
            due: dict[int, tuple[str, int]] = getattr(
                bot, "_custom_direct_virtual_due", {}
            )
            bot._custom_direct_virtual_due = due
            due[managed_id] = (
                str(signal.symbol),
                int(signal.tick_sequence)
                + max(1, int(getattr(signal, "duration_ticks", 1) or 1)),
            )
            bot.repository.mark_signal(
                signal.signal_id,
                status="VIRTUAL_OBSERVATION",
            )
            bot._set_account_execution_status(
                managed_id,
                "running",
                "Virtual protection observation is active; no monetary purchase was sent",
            )
            return

        predicted = float(nominal_probability(item.config))
        contract_id = await item.execution.execute_real(
            signal,
            predicted_probability=predicted,
            virtual_protection_enabled=bool(
                item.config.get("virtual_hook_enabled", True)
            ),
        )
        bot.repository.mark_signal(
            signal.signal_id,
            status="PURCHASE_CONFIRMED",
            purchase_requested=True,
            purchase_confirmed=True,
            expected_account_masks=[],
            registered_account_masks=[],
        )
        bot._set_account_execution_status(
            managed_id,
            "running",
            f"Contract {contract_id} is open and settlement monitoring is active",
        )
        bot._save_state()
    except asyncio.CancelledError:
        raise
    except AccountExecutionPreparationError as exc:
        _fail_closed(
            bot,
            managed_id,
            f"Trading stopped: {exc}",
            log_event="CUSTOM_STRATEGY_EXECUTION_PREPARATION_FAILED",
        )
    except AccountExecutionError as exc:
        _fail_closed(
            bot,
            managed_id,
            f"Trading stopped: {exc}",
            log_event="CUSTOM_STRATEGY_EXECUTION_FAILED",
        )
    except Exception as exc:
        _fail_closed(
            bot,
            managed_id,
            "Trading stopped: account execution failed safely.",
            log_event="CUSTOM_STRATEGY_EXECUTION_FAILED",
        )
        bot.logger.exception(
            "CUSTOM_STRATEGY_EXECUTION_EXCEPTION managed_id=%s error_type=%s",
            managed_id,
            type(exc).__name__,
        )
    finally:
        inflight.discard(managed_id)


def _schedule_account_matches(
    bot: RFDir5TradingBot,
    *,
    symbol: str,
    tick: dict[str, Any],
) -> None:
    market = bot.market_states.get(symbol)
    if market is None:
        return
    runtime: dict[int, DirectRuntimeAccount] = getattr(
        bot, "_custom_direct_accounts", {}
    )
    if not runtime:
        return
    digits = _digits(market)
    quotes = _quotes(market)
    inflight: set[int] = getattr(bot, "_custom_direct_inflight", set())
    tasks: set[asyncio.Task[Any]] = getattr(bot, "_custom_direct_tasks", set())
    bot._custom_direct_inflight = inflight
    bot._custom_direct_tasks = tasks

    for managed_id, item in list(runtime.items()):
        if managed_id in inflight:
            continue
        if _account_has_open_actual(item) or _account_has_open_virtual(bot, managed_id):
            continue
        if not market_selected(item.config, symbol):
            continue
        try:
            qualifies = evaluate_custom_strategy(
                item.config,
                digits=digits,
                quotes=quotes,
            )
        except (TypeError, ValueError):
            qualifies = False
        if not qualifies:
            continue

        fingerprint = custom_strategy_fingerprint(item.config)
        seen_key = (managed_id, symbol, int(market.tick_sequence), fingerprint)
        seen: set[tuple[int, str, int, str]] = getattr(
            bot, "_custom_direct_seen", set()
        )
        bot._custom_direct_seen = seen
        if seen_key in seen:
            continue
        seen.add(seen_key)
        if len(seen) > 3000:
            cutoff = int(market.tick_sequence) - 4
            bot._custom_direct_seen = {
                value for value in seen if int(value[2]) >= cutoff
            }

        signal = build_custom_signal(
            bot,
            symbol=symbol,
            tick=tick,
            config=item.config,
        )
        bot.repository.record_candidate(signal)
        inflight.add(managed_id)
        task = asyncio.create_task(
            _execute_for_account(bot, item, signal=signal),
            name=f"custom_direct_{managed_id}_{signal.signal_id}",
        )
        tasks.add(task)

        def _done(
            completed: asyncio.Task[Any],
            *,
            task_set: set[asyncio.Task[Any]] = tasks,
        ) -> None:
            task_set.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                return
            except Exception:
                LOGGER.exception("CUSTOM_DIRECT_TASK_FAILED")

        task.add_done_callback(_done)


async def _direct_on_tick(
    bot: RFDir5TradingBot,
    tick_data: dict[str, Any],
) -> None:
    tick = tick_data.get("tick") or {}
    symbol = str(tick.get("symbol") or "").strip()
    if not symbol or tick.get("quote") is None:
        return
    if symbol not in set(getattr(bot, "symbols", []) or []):
        return
    market = bot.market_states.get(symbol)
    if market is None:
        return

    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    tick_id = bot._tick_identity(symbol, epoch, quote)
    previous_epoch = int(bot.rf_last_epoch.get(symbol, -1) or -1)
    if (epoch > 0 and previous_epoch > 0 and epoch < previous_epoch) or tick_id == bot.rf_last_tick_id.get(symbol):
        return
    if epoch > 0:
        bot.rf_last_epoch[symbol] = epoch
    bot.rf_last_tick_id[symbol] = tick_id
    bot.live_market_symbol = symbol
    bot._mark_tick_received(market)

    display = f"{quote:.{market.pip_size}f}"
    digit: int | None = next(
        (int(character) for character in reversed(display) if character.isdigit()),
        None,
    )
    bot.tick_sequence += 1
    market.tick_sequence += 1
    snapshot = {
        "quote": quote,
        "display": display,
        "epoch": epoch,
        "tick_id": tick_id,
        "last_digit": digit if digit is not None else "-",
    }
    market.live_ticks_history.append(snapshot)
    market.ticks_history.append(snapshot)
    if digit is not None:
        market.raw_tick_digits.append(digit)

    # Intentionally no repository.record_tick(), no per-tick DB reads, and no
    # INFO tick log. The builder evaluates from bounded in-memory deques only.
    await _settle_due_virtuals(
        bot,
        symbol=symbol,
        market=market,
        quote=quote,
        epoch=epoch,
        digit=digit,
    )
    _schedule_account_matches(bot, symbol=symbol, tick=tick)


def _install_history_bootstrap() -> None:
    current_count = RFDir5TradingBot._public_history_count
    current_history = RFDir5TradingBot._on_public_history

    def history_count(self: RFDir5TradingBot) -> int:
        runtime: dict[int, DirectRuntimeAccount] = getattr(
            self, "_custom_direct_accounts", {}
        )
        if not runtime:
            return 0
        required = max(
            (
                int(condition.get("window") or 1)
                for item in runtime.values()
                for condition in item.config.get("conditions") or []
            ),
            default=1,
        )
        return min(MAX_WINDOW, max(1, required))

    def on_history(
        self: RFDir5TradingBot,
        *,
        symbol: str,
        prices: list[Any],
        times: list[Any],
        pip_size: Any,
    ) -> None:
        market = self.market_states.get(symbol)
        if market is None:
            return
        try:
            market.pip_size = int(pip_size or market.pip_size)
        except (TypeError, ValueError):
            pass
        market.ticks_history.clear()
        market.raw_tick_digits.clear()
        for index, raw_quote in enumerate(prices[-MAX_WINDOW:]):
            quote = Decimal(str(raw_quote))
            display = f"{quote:.{market.pip_size}f}"
            digit = next(
                (int(character) for character in reversed(display) if character.isdigit()),
                None,
            )
            epoch = 0
            if index < len(times):
                try:
                    epoch = int(times[-len(prices[-MAX_WINDOW:]) + index])
                except (TypeError, ValueError, IndexError):
                    epoch = 0
            market.ticks_history.append(
                {
                    "quote": quote,
                    "display": display,
                    "epoch": epoch,
                    "tick_id": f"history:{symbol}:{epoch}:{index}",
                    "last_digit": digit if digit is not None else "-",
                }
            )
            if digit is not None:
                market.raw_tick_digits.append(digit)

    RFDir5TradingBot._public_history_count = history_count
    RFDir5TradingBot._on_public_history = on_history
    RFDir5TradingBot._custom_direct_previous_history_count = current_count
    RFDir5TradingBot._custom_direct_previous_history_handler = current_history


def install_custom_strategy_direct_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_init = RFDir5TradingBot.__init__
    original_wait = RFDir5TradingBot._wait_for_active_execution_account
    original_refresh = RFDir5TradingBot._refresh_runtime_accounts_if_needed
    original_private_ready = RFDir5TradingBot._on_private_session_ready
    original_contract_update = RFDir5TradingBot.handle_contract_update

    def direct_init(self: RFDir5TradingBot, config_path: str | None = None) -> None:
        original_init(self, config_path)
        self._custom_direct_accounts: dict[int, DirectRuntimeAccount] = {}
        self._custom_direct_inflight: set[int] = set()
        self._custom_direct_tasks: set[asyncio.Task[Any]] = set()
        self._custom_direct_seen: set[tuple[int, str, int, str]] = set()
        self._custom_direct_virtual_due: dict[int, tuple[str, int]] = {}
        self.logger.warning(
            "CUSTOM_DIRECT_RUNTIME_ACTIVE path=CustomStrategyRuntime->AccountExecutionSession->"
            "proposal->buy_proposal_id->register->settlement legacy_router=false "
            "rotating_cohorts=false tick_db_persistence=false"
        )

    async def wait_for_direct_account(self: RFDir5TradingBot) -> bool:
        # Do not start the public scanner until at least one exact account has a
        # valid credential, synchronized client state, connected private session,
        # and configured custom strategy.
        while self.is_running:
            try:
                await self.validate_accounts()
                self._sync_clients_with_runtime_accounts()
                await self._ensure_sessions_for_valid_clients()
                runtime = _refresh_direct_accounts(
                    self,
                    require_connected=True,
                    fail_invalid=True,
                )
                if runtime:
                    for managed_id in runtime:
                        self._set_account_execution_status(
                            managed_id,
                            "waiting_for_condition",
                            "Execution session ready; waiting for the configured Custom Strategy condition",
                        )
                    self._managed_accounts_revision = self.repository.managed_accounts_revision()
                    self._runtime_mode_cache = self.environment
                    return True
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("CUSTOM_RUNTIME_START_PREPARATION_FAILED")
            await asyncio.sleep(0.75)
        return False

    async def refresh_direct_accounts(self: RFDir5TradingBot) -> None:
        before = tuple(getattr(self, "symbols", []) or [])
        await original_refresh(self)
        self._sync_clients_with_runtime_accounts()
        await self._ensure_sessions_for_valid_clients()
        runtime = _refresh_direct_accounts(
            self,
            require_connected=True,
            fail_invalid=True,
        )
        for managed_id in runtime:
            account = runtime[managed_id]
            if not _account_has_open_actual(account) and not _account_has_open_virtual(
                self, managed_id
            ):
                self._set_account_execution_status(
                    managed_id,
                    "waiting_for_condition",
                    "Execution session ready; waiting for the configured Custom Strategy condition",
                )
        after = tuple(getattr(self, "symbols", []) or [])
        if before != after and bool(getattr(self.public_client, "is_connected", False)):
            await self.public_client.request_reconnect("custom_market_set_changed")

    def private_ready(self: RFDir5TradingBot, session: Any) -> None:
        original_private_ready(self, session)
        managed_id = getattr(session, "managed_account_id", None)
        if managed_id is None:
            return
        try:
            self._sync_clients_with_runtime_accounts()
            runtime = _refresh_direct_accounts(
                self,
                require_connected=True,
                fail_invalid=False,
            )
            if int(managed_id) in runtime:
                self._set_account_execution_status(
                    int(managed_id),
                    "waiting_for_condition",
                    "Authenticated account execution session is ready",
                )
        except Exception:
            self.logger.exception(
                "CUSTOM_PRIVATE_READY_VALIDATION_FAILED managed_id=%s",
                managed_id,
            )

    async def contract_update(
        self: RFDir5TradingBot,
        token: str,
        contract_id: int,
        contract: dict[str, Any],
    ) -> None:
        await original_contract_update(self, token, contract_id, contract)
        if not self._contract_is_terminal(contract):
            return
        managed_id = self._managed_account_id_for_token(token)
        if managed_id is None:
            return
        account = self.repository.managed_account(int(managed_id)) or {}
        if bool(account.get("enabled")):
            self._set_account_execution_status(
                int(managed_id),
                "waiting_for_condition",
                "Previous contract settled; waiting for the next qualifying Custom Strategy condition",
            )

    RFDir5TradingBot.__init__ = direct_init
    RFDir5TradingBot._wait_for_active_execution_account = wait_for_direct_account
    RFDir5TradingBot._refresh_runtime_accounts_if_needed = refresh_direct_accounts
    RFDir5TradingBot._on_private_session_ready = private_ready
    RFDir5TradingBot.handle_contract_update = contract_update
    RFDir5TradingBot._on_tick = _direct_on_tick
    _install_history_bootstrap()
    RFDir5TradingBot._custom_strategy_direct_runtime_installed = True
    _INSTALLED = True
