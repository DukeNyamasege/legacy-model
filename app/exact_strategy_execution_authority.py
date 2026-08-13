from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app import custom_strategy_direct_runtime as direct_runtime
from app.account_execution_session import (
    AccountExecutionError,
    AccountExecutionPreparationError,
    AccountExecutionSession,
)
from app.custom_strategy_v1 import (
    contract_for_config,
    custom_strategy_fingerprint,
    evaluate_custom_strategy,
    market_selected,
    nominal_probability,
)
from app.models import VirtualTrade, utc_now
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_ORIGINAL_PROPOSAL: Any = None
_ORIGINAL_BUY_PROPOSAL: Any = None
_ORIGINAL_EXECUTE_REAL: Any = None
_ACTIVE_SIGNALS: dict[tuple[int, str], Any] = {}


class ExactSignalExpired(AccountExecutionError):
    """The configured pattern qualified, but its trigger tick is no longer current."""


class ExactStrategyMismatch(AccountExecutionError):
    """The generated signal no longer represents the account's exact saved strategy."""


def _execution_key(session: AccountExecutionSession) -> tuple[int, str]:
    return int(session.managed_account_id), str(session.token)


def _assert_trigger_tick_current(session: AccountExecutionSession, signal: Any) -> None:
    symbol = str(getattr(signal, "symbol", "") or "")
    market = getattr(session.bot, "market_states", {}).get(symbol)
    if market is None:
        raise ExactSignalExpired(f"exact-entry market {symbol or '-'} is unavailable")
    expected_sequence = int(getattr(signal, "tick_sequence", -1))
    current_sequence = int(getattr(market, "tick_sequence", -2))
    if current_sequence != expected_sequence:
        raise ExactSignalExpired(
            "exact-entry trigger tick expired before purchase "
            f"market={symbol} trigger_sequence={expected_sequence} "
            f"current_sequence={current_sequence}"
        )


def _assert_strategy_exact(item: Any, signal: Any) -> None:
    """Re-validate every saved condition and the exact contract on the trigger tick."""

    config = item.config
    symbol = str(getattr(signal, "symbol", "") or "")
    _assert_trigger_tick_current(item.execution, signal)
    if not market_selected(config, symbol):
        raise ExactStrategyMismatch(f"market {symbol or '-'} is not selected by this strategy")

    market = item.execution.bot.market_states.get(symbol)
    if market is None:
        raise ExactSignalExpired(f"exact-entry market {symbol or '-'} is unavailable")
    if not evaluate_custom_strategy(
        config,
        digits=direct_runtime._digits(market),
        quotes=direct_runtime._quotes(market),
    ):
        raise ExactStrategyMismatch(
            "saved Custom Strategy conditions are not all true on the trigger tick"
        )

    contract_type, _direction, barrier = contract_for_config(config)
    configured_duration = max(1, int(config.get("duration_ticks") or 1))
    signal_duration = max(1, int(getattr(signal, "duration_ticks", 1) or 1))
    if str(getattr(signal, "contract_type", "") or "").upper() != str(contract_type).upper():
        raise ExactStrategyMismatch("signal contract type does not match saved Custom Strategy")
    if str(getattr(signal, "barrier", "") or "") != str(barrier or ""):
        raise ExactStrategyMismatch("signal prediction/barrier does not match saved Custom Strategy")
    if signal_duration != configured_duration:
        raise ExactStrategyMismatch("signal duration does not match saved Custom Strategy")

    expected_trigger = f"CUSTOM-V2-{custom_strategy_fingerprint(config)[:8].upper()}"
    if str(getattr(signal, "trigger_name", "") or "") != expected_trigger:
        raise ExactStrategyMismatch("signal fingerprint does not match saved Custom Strategy")


async def _proposal_with_exact_tick(
    self: AccountExecutionSession,
    signal: Any,
    *,
    stake: float,
    predicted_probability: float,
) -> Any:
    _assert_trigger_tick_current(self, signal)
    result = await _ORIGINAL_PROPOSAL(
        self,
        signal,
        stake=stake,
        predicted_probability=predicted_probability,
    )
    _assert_trigger_tick_current(self, signal)
    return result


async def _buy_with_exact_tick(
    self: AccountExecutionSession,
    economics: Any,
) -> dict[str, Any]:
    signal = _ACTIVE_SIGNALS.get(_execution_key(self))
    if signal is not None:
        # This is the last local guard before the private WebSocket BUY request.
        # If a new market tick already arrived, skip rather than buy late.
        _assert_trigger_tick_current(self, signal)
    return await _ORIGINAL_BUY_PROPOSAL(self, economics)


async def _execute_real_with_exact_tick(
    self: AccountExecutionSession,
    signal: Any,
    *,
    predicted_probability: float,
    virtual_protection_enabled: bool,
) -> int:
    _assert_trigger_tick_current(self, signal)
    key = _execution_key(self)
    _ACTIVE_SIGNALS[key] = signal
    try:
        result = await _ORIGINAL_EXECUTE_REAL(
            self,
            signal,
            predicted_probability=predicted_probability,
            virtual_protection_enabled=virtual_protection_enabled,
        )
        return int(result)
    finally:
        _ACTIVE_SIGNALS.pop(key, None)


def _expire_overdue_virtuals(
    bot: RFDir5TradingBot,
    *,
    symbol: str,
    tick_sequence: int,
    managed_ids: set[int],
) -> int:
    if not managed_ids:
        return 0
    expired = 0
    with bot.repository.database.session() as session:
        rows = session.scalars(
            select(VirtualTrade)
            .where(
                VirtualTrade.run_id == bot.repository.run_id,
                VirtualTrade.market == str(symbol),
                VirtualTrade.result == "OPEN",
                VirtualTrade.managed_account_id.in_(managed_ids),
                VirtualTrade.exit_tick_sequence < int(tick_sequence),
            )
            .with_for_update()
        ).all()
        now = utc_now()
        for row in rows:
            row.result = "VIRTUAL_STALE"
            row.reason = (
                "Exact virtual exit tick was missed; observation discarded instead of "
                "settling on a later digit"
            )
            row.amount_charged = 0.0
            row.actual_profit_loss = 0.0
            row.actual_payout = 0.0
            row.recovery_debt_change = 0.0
            row.settled_at = now
            expired += 1
    return expired


async def _settle_virtuals_on_exact_tick(
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
    current_sequence = int(market.tick_sequence)

    overdue = {
        managed_id
        for managed_id, (due_symbol, exit_sequence) in due.items()
        if due_symbol == symbol and int(exit_sequence) < current_sequence
    }
    if overdue:
        expired_count = _expire_overdue_virtuals(
            bot,
            symbol=symbol,
            tick_sequence=current_sequence,
            managed_ids={int(value) for value in overdue},
        )
        for managed_id in overdue:
            due.pop(managed_id, None)
            account = getattr(bot, "_custom_direct_accounts", {}).get(managed_id)
            if account is not None:
                bot._set_account_execution_status(
                    managed_id,
                    "waiting_for_condition",
                    "Exact virtual exit tick was missed; waiting for the next qualifying pattern",
                )
        bot.logger.warning(
            "CUSTOM_VIRTUAL_EXACT_EXIT_MISSED market=%s tick_sequence=%s "
            "accounts=%s expired_rows=%s financial_impact=0",
            symbol,
            current_sequence,
            len(overdue),
            expired_count,
        )

    exact_due = {
        managed_id
        for managed_id, (due_symbol, exit_sequence) in due.items()
        if due_symbol == symbol and int(exit_sequence) == current_sequence
    }
    if not exact_due:
        return

    settled = list(
        bot.rf_repository.settle_due_virtual_trades(
            symbol=symbol,
            tick_sequence=current_sequence,
            exit_quote=quote,
            exit_epoch=epoch,
            exit_digit=digit,
            exit_after_wins=1,
            max_observations=0,
        )
        or []
    )
    for managed_id in exact_due:
        due.pop(managed_id, None)
        account = getattr(bot, "_custom_direct_accounts", {}).get(managed_id)
        if account is not None:
            bot._set_account_execution_status(
                managed_id,
                "waiting_for_condition",
                "Virtual observation settled on its exact exit tick; waiting for the next qualifying pattern",
            )
    for payload in settled:
        bot.logger.info(
            "CUSTOM_VIRTUAL_TRADE_SETTLED account=%s market=%s result=%s "
            "exact_exit_sequence=%s actual_financial_impact=0",
            payload.get("account", "account"),
            payload.get("market", symbol),
            payload.get("result", "unknown"),
            current_sequence,
        )
    if settled:
        try:
            await bot._notify_dashboard_settlement()
        except Exception:
            bot.logger.exception("CUSTOM_VIRTUAL_DASHBOARD_NOTIFY_FAILED")


async def _execute_exact_for_account(
    bot: RFDir5TradingBot,
    item: Any,
    *,
    signal: Any,
) -> None:
    managed_id = int(item.managed_id)
    inflight: set[int] = getattr(bot, "_custom_direct_inflight", set())
    try:
        item.execution.prepare()
        _assert_strategy_exact(item, signal)
        bot._set_account_execution_status(
            managed_id,
            "executing",
            "Custom Strategy qualified; exact trigger tick is being executed",
        )
        bot.logger.info(
            "CUSTOM_STRATEGY_SIGNAL_QUALIFIED signal_id=%s managed_id=%s symbol=%s "
            "trade_type=%s contract_type=%s barrier=%s duration_ticks=%s conditions=%s "
            "entry_gate=user_custom_pattern condition_join=AND exact_tick_sequence=%s",
            signal.signal_id,
            managed_id,
            signal.symbol,
            item.config.get("trade_type"),
            signal.contract_type,
            getattr(signal, "barrier", "") or "-",
            max(1, int(getattr(signal, "duration_ticks", 1) or 1)),
            len(item.config.get("conditions") or []),
            int(getattr(signal, "tick_sequence", -1)),
        )

        protection = bot.rf_repository.virtual_protection_for_account(
            managed_account_id=managed_id,
            account_id_masked="",
        )
        if (
            bool(item.config.get("virtual_hook_enabled", True))
            and str(protection.get("mode") or "") == "VIRTUAL_MODE"
        ):
            _assert_strategy_exact(item, signal)
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
            bot.repository.mark_signal(signal.signal_id, status="VIRTUAL_OBSERVATION")
            bot._set_account_execution_status(
                managed_id,
                "running",
                "Exact Custom Strategy virtual observation is active; financial impact is 0",
            )
            bot.logger.info(
                "CUSTOM_VIRTUAL_EXACT_ENTRY signal_id=%s managed_id=%s market=%s "
                "contract_type=%s barrier=%s entry_sequence=%s exit_sequence=%s "
                "amount_charged=0 payout=0 profit_loss=0",
                signal.signal_id,
                managed_id,
                signal.symbol,
                signal.contract_type,
                getattr(signal, "barrier", "") or "-",
                int(signal.tick_sequence),
                due[managed_id][1],
            )
            return

        predicted = float(nominal_probability(item.config))
        contract_id = await item.execution.execute_real(
            signal,
            predicted_probability=predicted,
            virtual_protection_enabled=bool(item.config.get("virtual_hook_enabled", True)),
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
    except (ExactSignalExpired, ExactStrategyMismatch) as exc:
        try:
            bot.repository.mark_signal(signal.signal_id, status="EXPIRED_BEFORE_ENTRY")
        except Exception:
            pass
        bot._set_account_execution_status(
            managed_id,
            "waiting_for_condition",
            "Qualified pattern was not purchased because the exact trigger tick had already moved; waiting for the next exact match",
        )
        bot.logger.info(
            "CUSTOM_STRATEGY_EXACT_ENTRY_SKIPPED signal_id=%s managed_id=%s symbol=%s "
            "reason=%s financial_purchase=false",
            signal.signal_id,
            managed_id,
            signal.symbol,
            str(exc)[:180],
        )
    except AccountExecutionPreparationError as exc:
        direct_runtime._fail_closed(
            bot,
            managed_id,
            f"Trading stopped: {exc}",
            log_event="CUSTOM_STRATEGY_EXECUTION_PREPARATION_FAILED",
        )
    except AccountExecutionError as exc:
        direct_runtime._fail_closed(
            bot,
            managed_id,
            f"Trading stopped: {exc}",
            log_event="CUSTOM_STRATEGY_EXECUTION_FAILED",
        )
    except Exception as exc:
        direct_runtime._fail_closed(
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


def install_exact_strategy_execution_authority() -> None:
    """Make Custom Strategy entry correctness stronger than late execution.

    Every condition is rechecked on the same qualifying tick. Proposal and BUY keep
    the existing exact authenticated account session, but a BUY is skipped if a new
    tick has already arrived. Virtual Hook uses the same signal/contract and exact
    entry/exit tick sequence with zero financial impact; a missed virtual exit tick
    is discarded rather than settled against a later digit.
    """

    global _INSTALLED, _ORIGINAL_PROPOSAL, _ORIGINAL_BUY_PROPOSAL, _ORIGINAL_EXECUTE_REAL
    if _INSTALLED:
        return

    _ORIGINAL_PROPOSAL = AccountExecutionSession.proposal
    _ORIGINAL_BUY_PROPOSAL = AccountExecutionSession.buy_proposal
    _ORIGINAL_EXECUTE_REAL = AccountExecutionSession.execute_real

    AccountExecutionSession.proposal = _proposal_with_exact_tick  # type: ignore[method-assign]
    AccountExecutionSession.buy_proposal = _buy_with_exact_tick  # type: ignore[method-assign]
    AccountExecutionSession.execute_real = _execute_real_with_exact_tick  # type: ignore[method-assign]
    direct_runtime._execute_for_account = _execute_exact_for_account
    direct_runtime._settle_due_virtuals = _settle_virtuals_on_exact_tick
    RFDir5TradingBot._exact_strategy_execution_authority_installed = True
    _INSTALLED = True
