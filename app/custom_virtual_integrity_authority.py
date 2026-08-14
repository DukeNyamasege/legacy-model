from __future__ import annotations

from collections import deque
from decimal import Decimal
import logging
from typing import Any

from sqlalchemy import select

from app import custom_strategy_direct_runtime as direct_runtime
from app.custom_strategy_settlement import custom_virtual_outcome
from app.custom_strategy_v1 import read_custom_strategy
from app.custom_strategy_virtual_hook import virtual_hook_settings_from_session
from app.models import AccountRiskState, VirtualTrade, utc_now
from app.repositories.rf_dir5_repository import (
    REAL_RECOVERY_PENDING,
    RFDir5Repository,
    VIRTUAL_LOSS,
    VIRTUAL_WAITING_FOR_WIN,
    VIRTUAL_WIN,
)
from app.rf_dir5_bot import RFDir5TradingBot


LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ORIGINAL_REFRESH: Any = None
_ORIGINAL_SCHEDULE: Any = None
_ORIGINAL_REQUIRED_WINS: Any = None


def _configured_required_wins(
    repository: RFDir5Repository,
    managed_account_id: int,
    *,
    session: Any | None = None,
) -> int | None:
    """Return the exact Custom Strategy virtual-win setting when configured."""

    try:
        config = read_custom_strategy(repository.database, int(managed_account_id))
        if not bool(config.get("configured")):
            return None
        if session is not None:
            hook = virtual_hook_settings_from_session(session, int(managed_account_id))
        else:
            with repository.database.session() as lookup:
                hook = virtual_hook_settings_from_session(lookup, int(managed_account_id))
        if not hook.enabled:
            return 1
        return max(1, int(hook.exit_after_consecutive_wins))
    except Exception:
        return None


def _required_wins_without_aidr_escalation(
    self: RFDir5Repository,
    managed_account_id: int,
    *,
    recovery_debt: float = 0.0,
    default_wins: int = 1,
    session: Any | None = None,
) -> int:
    """Custom Strategy obeys the saved hook, while non-custom callers keep legacy policy."""

    configured = _configured_required_wins(
        self,
        int(managed_account_id),
        session=session,
    )
    if configured is not None:
        return configured
    original = _ORIGINAL_REQUIRED_WINS
    if original is None:
        return max(1, int(default_wins or 1))
    return int(
        original(
            self,
            int(managed_account_id),
            recovery_debt=float(recovery_debt or 0.0),
            default_wins=max(1, int(default_wins or 1)),
            session=session,
        )
    )


def _restore_open_virtuals(bot: RFDir5TradingBot, managed_ids: set[int]) -> set[int]:
    """Rebuild the in-memory virtual lock from PostgreSQL after refresh/reconnect."""

    open_ids: set[int] = set()
    due: dict[int, tuple[str, int]] = getattr(bot, "_custom_direct_virtual_due", {})
    bot._custom_direct_virtual_due = due
    if not managed_ids:
        bot._custom_virtual_open_ids = open_ids
        return open_ids

    with bot.repository.database.session() as session:
        rows = session.scalars(
            select(VirtualTrade).where(
                VirtualTrade.managed_account_id.in_(list(managed_ids)),
                VirtualTrade.result == "OPEN",
            )
        ).all()
    for row in rows:
        managed_id = int(row.managed_account_id)
        open_ids.add(managed_id)
        due[managed_id] = (str(row.market), int(row.exit_tick_sequence))

    bot._custom_virtual_open_ids = open_ids
    return open_ids


def _persistent_open_virtual(bot: RFDir5TradingBot, managed_id: int) -> bool:
    """Hot-path check backed by the PostgreSQL state synchronized on refresh."""

    managed = int(managed_id)
    if managed in set(getattr(bot, "_custom_virtual_open_ids", set()) or set()):
        return True
    return managed in dict(getattr(bot, "_custom_direct_virtual_due", {}) or {})


def _refresh_with_virtual_lock(
    bot: RFDir5TradingBot,
    *,
    require_connected: bool,
    fail_invalid: bool,
) -> dict[int, Any]:
    original = _ORIGINAL_REFRESH
    if original is None:
        return {}
    runtime = original(
        bot,
        require_connected=require_connected,
        fail_invalid=fail_invalid,
    )
    open_ids = _restore_open_virtuals(bot, {int(value) for value in runtime})
    for managed_id in open_ids:
        if managed_id in runtime:
            bot._set_account_execution_status(
                managed_id,
                "virtual_protection",
                "Zero-stake virtual observation is still active; real execution remains blocked until it settles",
            )
    return runtime


def _buffer_for(bot: RFDir5TradingBot, symbol: str) -> deque[tuple[int, Decimal, int, int | None]]:
    buffers = getattr(bot, "_custom_virtual_tick_buffers", None)
    if not isinstance(buffers, dict):
        buffers = {}
        bot._custom_virtual_tick_buffers = buffers
    buffer = buffers.get(str(symbol))
    if not isinstance(buffer, deque):
        buffer = deque(maxlen=64)
        buffers[str(symbol)] = buffer
    return buffer


def _snapshot_for_sequence(
    bot: RFDir5TradingBot,
    symbol: str,
    sequence: int,
) -> tuple[Decimal, int, int | None] | None:
    for item_sequence, quote, epoch, digit in reversed(_buffer_for(bot, symbol)):
        if int(item_sequence) == int(sequence):
            return quote, int(epoch), digit
    return None


def _void_for_retry(
    bot: RFDir5TradingBot,
    trade: VirtualTrade,
    state: AccountRiskState | None,
    *,
    reason: str,
) -> None:
    """Void an unobservable $0 sample without unlocking a real purchase."""

    trade.result = "VIRTUAL_VOID_RETRY"
    trade.reason = f"{reason} | No purchase | confirmation not counted | retry required"
    trade.amount_charged = 0.0
    trade.actual_profit_loss = 0.0
    trade.actual_payout = 0.0
    trade.recovery_debt_change = 0.0
    trade.settled_at = utc_now()
    if state is not None and float(state.recovery_loss_debt or 0.0) >= 0.01:
        state.protection_mode = VIRTUAL_WAITING_FOR_WIN
        state.updated_at = utc_now()
    bot.logger.warning(
        "CUSTOM_VIRTUAL_VOID_RETRY managed_id=%s market=%s virtual_trade_id=%s "
        "reason=%s actual_financial_impact=0 real_execution_unlocked=false",
        int(trade.managed_account_id),
        str(trade.market),
        str(trade.virtual_trade_id),
        reason,
    )


def _settle_one(
    bot: RFDir5TradingBot,
    trade: VirtualTrade,
    state: AccountRiskState,
    *,
    quote: Decimal,
    epoch: int,
    digit: int | None,
) -> str:
    """Settle one virtual mirror against its own exact buffered exit tick."""

    outcome, actual_exit_digit = custom_virtual_outcome(
        direction=str(trade.direction or ""),
        contract_type=str(trade.contract_type or ""),
        barrier=trade.barrier,
        prediction_digit=trade.prediction_digit,
        entry_quote=Decimal(str(trade.entry_spot)),
        exit_quote=Decimal(str(quote)),
        exit_digit=digit,
    )
    result = VIRTUAL_WIN if outcome == "WIN" else VIRTUAL_LOSS
    required = _configured_required_wins(
        bot.rf_repository,
        int(trade.managed_account_id),
    )
    if required is None:
        required = max(1, int(bot.rf_repository._required_virtual_wins(
            int(trade.managed_account_id),
            recovery_debt=float(state.recovery_loss_debt or 0.0),
            default_wins=1,
        )))

    consecutive = int(state.virtual_win_count or 0) + 1 if result == VIRTUAL_WIN else 0
    trade.exit_spot = float(quote)
    trade.exit_tick_epoch = int(epoch or 0)
    trade.actual_last_digit = actual_exit_digit
    trade.result = result
    trade.amount_charged = 0.0
    trade.actual_profit_loss = 0.0
    trade.actual_payout = 0.0
    trade.recovery_debt_change = 0.0
    trade.settled_at = utc_now()
    state.virtual_observation_count += 1

    if result == VIRTUAL_WIN:
        state.virtual_win_count = consecutive
        state.current_virtual_loss_streak = 0
    else:
        state.virtual_win_count = 0
        state.virtual_loss_count += 1
        state.current_virtual_loss_streak += 1

    trade.reason = (
        "Hypothetical Outcome - No Purchase | "
        f"progress={consecutive if result == VIRTUAL_WIN else 0}/{required}"
    )
    if result == VIRTUAL_WIN and consecutive >= required:
        state.protection_mode = REAL_RECOVERY_PENDING
        state.recovery_pending = bool(float(state.recovery_loss_debt or 0.0) >= 0.01)
        if state.recovery_pending and state.recovery_pending_since is None:
            state.recovery_pending_since = utc_now()
    else:
        state.protection_mode = VIRTUAL_WAITING_FOR_WIN
    state.updated_at = utc_now()
    return result


async def _settle_virtual_mirror(
    bot: RFDir5TradingBot,
    *,
    symbol: str,
    market: Any,
    quote: Decimal,
    epoch: int,
    digit: int | None,
) -> None:
    """Resolve virtual samples from their own ticks and never unlock real on a void."""

    current_sequence = int(market.tick_sequence)
    _buffer_for(bot, symbol).append((current_sequence, Decimal(str(quote)), int(epoch or 0), digit))

    due = dict(getattr(bot, "_custom_direct_virtual_due", {}) or {})
    tracked_ids = {
        int(managed_id)
        for managed_id, (due_symbol, _exit_sequence) in due.items()
        if str(due_symbol) == str(symbol)
    }
    if not tracked_ids:
        return

    settled_ids: set[int] = set()
    void_ids: set[int] = set()
    results: list[tuple[int, str, int]] = []
    with bot.repository.database.session() as session:
        rows = session.scalars(
            select(VirtualTrade)
            .where(
                VirtualTrade.managed_account_id.in_(list(tracked_ids)),
                VirtualTrade.market == str(symbol),
                VirtualTrade.result == "OPEN",
            )
            .with_for_update()
        ).all()
        rows_by_id = {int(row.managed_account_id): row for row in rows}

        for managed_id in tracked_ids:
            trade = rows_by_id.get(managed_id)
            if trade is None:
                settled_ids.add(managed_id)
                continue
            state = session.get(AccountRiskState, managed_id, with_for_update=True)
            exit_sequence = int(trade.exit_tick_sequence)

            # A worker restart resets the in-memory market sequence. We cannot know
            # the old process's missing exit tick, so void the zero-cost sample and
            # remain in virtual mode instead of pretending it was cancelled/won.
            if int(trade.entry_tick_sequence) > current_sequence:
                _void_for_retry(
                    bot,
                    trade,
                    state,
                    reason="Worker restarted before the virtual exit tick could be observed",
                )
                void_ids.add(managed_id)
                continue
            if exit_sequence > current_sequence:
                continue

            snapshot = _snapshot_for_sequence(bot, symbol, exit_sequence)
            if snapshot is None:
                _void_for_retry(
                    bot,
                    trade,
                    state,
                    reason="Exact virtual exit tick was unavailable after a market-stream interruption",
                )
                void_ids.add(managed_id)
                continue
            if state is None:
                _void_for_retry(
                    bot,
                    trade,
                    None,
                    reason="Virtual protection state was unavailable at settlement",
                )
                void_ids.add(managed_id)
                continue

            if state.protection_mode != VIRTUAL_WAITING_FOR_WIN:
                # An OPEN virtual is stronger evidence than a prematurely advanced
                # mode. Fail toward safety: finish the $0 observation before money.
                if float(state.recovery_loss_debt or 0.0) >= 0.01:
                    bot.logger.warning(
                        "CUSTOM_VIRTUAL_MODE_RACE_REPAIRED managed_id=%s old_mode=%s "
                        "new_mode=%s real_execution_unlocked=false",
                        managed_id,
                        str(state.protection_mode),
                        VIRTUAL_WAITING_FOR_WIN,
                    )
                    state.protection_mode = VIRTUAL_WAITING_FOR_WIN
                else:
                    _void_for_retry(
                        bot,
                        trade,
                        state,
                        reason="Virtual observation no longer belongs to an active recovery cycle",
                    )
                    void_ids.add(managed_id)
                    continue

            exit_quote, exit_epoch, exit_digit = snapshot
            try:
                result = _settle_one(
                    bot,
                    trade,
                    state,
                    quote=exit_quote,
                    epoch=exit_epoch,
                    digit=exit_digit,
                )
            except (TypeError, ValueError) as exc:
                _void_for_retry(
                    bot,
                    trade,
                    state,
                    reason=f"Virtual contract could not be evaluated safely: {type(exc).__name__}",
                )
                void_ids.add(managed_id)
                continue
            settled_ids.add(managed_id)
            results.append((managed_id, result, exit_sequence))

    live_due: dict[int, tuple[str, int]] = getattr(bot, "_custom_direct_virtual_due", {})
    open_ids: set[int] = getattr(bot, "_custom_virtual_open_ids", set())
    bot._custom_virtual_open_ids = open_ids
    resume_after: dict[int, tuple[str, int]] = getattr(bot, "_custom_virtual_resume_after", {})
    bot._custom_virtual_resume_after = resume_after
    for managed_id in settled_ids | void_ids:
        live_due.pop(managed_id, None)
        open_ids.discard(managed_id)
        # Never re-enter virtual or real on the same market tick that closed/voided
        # the previous zero-cost observation. The next *future* qualifying tick wins.
        resume_after[managed_id] = (str(symbol), current_sequence)

    for managed_id, result, exit_sequence in results:
        protection = bot.rf_repository.virtual_protection_for_account(
            managed_account_id=managed_id,
            account_id_masked="",
        )
        mode = str(protection.get("mode") or "")
        if mode == "RECOVERY_PENDING":
            status = "recovery_pending"
            reason = "Virtual mirror won; the next future qualifying signal may execute the real recovery trade"
        else:
            status = "virtual_protection"
            reason = "Virtual mirror settled; waiting for the next future qualifying zero-stake signal"
        bot._set_account_execution_status(managed_id, status, reason)
        bot.logger.info(
            "CUSTOM_VIRTUAL_MIRROR_SETTLED managed_id=%s market=%s result=%s "
            "exit_sequence=%s configured_wins_required=%s amount_charged=0",
            managed_id,
            symbol,
            result,
            exit_sequence,
            protection.get("virtual_wins_required", 1),
        )

    if results or void_ids:
        try:
            await bot._notify_dashboard_settlement()
        except Exception:
            bot.logger.exception("CUSTOM_VIRTUAL_DASHBOARD_NOTIFY_FAILED")


def _schedule_after_virtual_barrier(
    bot: RFDir5TradingBot,
    *,
    symbol: str,
    tick: dict[str, Any],
) -> None:
    original = _ORIGINAL_SCHEDULE
    if original is None:
        return
    runtime: dict[int, Any] = getattr(bot, "_custom_direct_accounts", {})
    if not runtime:
        return original(bot, symbol=symbol, tick=tick)

    market = bot.market_states.get(symbol)
    current_sequence = int(getattr(market, "tick_sequence", 0) or 0)
    resume_after: dict[int, tuple[str, int]] = getattr(bot, "_custom_virtual_resume_after", {})
    blocked: set[int] = set()
    for managed_id, (barrier_symbol, barrier_sequence) in list(resume_after.items()):
        if str(barrier_symbol) != str(symbol):
            continue
        if current_sequence <= int(barrier_sequence):
            blocked.add(int(managed_id))
        else:
            resume_after.pop(int(managed_id), None)

    if not blocked:
        return original(bot, symbol=symbol, tick=tick)

    bot._custom_direct_accounts = {
        int(managed_id): item
        for managed_id, item in runtime.items()
        if int(managed_id) not in blocked
    }
    try:
        original(bot, symbol=symbol, tick=tick)
    finally:
        bot._custom_direct_accounts = runtime


def install_custom_virtual_integrity_authority() -> None:
    """Make Custom Virtual Hook the same qualified trade with zero financial stake."""

    global _INSTALLED, _ORIGINAL_REFRESH, _ORIGINAL_SCHEDULE, _ORIGINAL_REQUIRED_WINS
    if _INSTALLED:
        return

    _ORIGINAL_REFRESH = direct_runtime._refresh_direct_accounts
    _ORIGINAL_SCHEDULE = direct_runtime._schedule_account_matches
    _ORIGINAL_REQUIRED_WINS = RFDir5Repository._required_virtual_wins

    RFDir5Repository._required_virtual_wins = _required_wins_without_aidr_escalation  # type: ignore[method-assign]
    direct_runtime._refresh_direct_accounts = _refresh_with_virtual_lock
    direct_runtime._account_has_open_virtual = _persistent_open_virtual
    direct_runtime._settle_due_virtuals = _settle_virtual_mirror
    direct_runtime._schedule_account_matches = _schedule_after_virtual_barrier

    RFDir5TradingBot._custom_virtual_integrity_authority_installed = True
    _INSTALLED = True
