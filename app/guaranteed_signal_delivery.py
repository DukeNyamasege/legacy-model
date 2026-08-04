from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.hybrid_digit_put as hybrid
import app.multi_strategy_runtime as multi
import app.standardized_execution_runtime as standardized
import app.standardized_signal_metadata as signal_metadata
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
GUARANTEED_SIGNAL_DELIVERY_VERSION = "qualified-cycle-delivery-v1"
_DELIVERY_STATE_TTL_SECONDS = 3600.0
_DELIVERY_STATE: dict[tuple[type[Any], str], dict[str, Any]] = {}


class _LiveTickSequence:
    """Resolve a standardized signal's sequence at the instant it is inspected.

    The legacy purchase method performs two equality checks while it prepares every
    account. On a one-second market, a valid signal could fail the second check only
    because account preparation crossed a tick boundary. This object makes those
    internal checks compare against the current sequence. It is converted back to a
    concrete integer immediately before private WebSocket purchases begin.
    """

    __slots__ = ("bot", "symbol", "fallback")

    def __init__(self, bot: RFDir5TradingBot, symbol: str, fallback: int) -> None:
        self.bot = bot
        self.symbol = str(symbol)
        self.fallback = int(fallback)

    def value(self) -> int:
        market = getattr(self.bot, "market_states", {}).get(self.symbol)
        if market is None:
            return self.fallback
        try:
            return int(getattr(market, "tick_sequence", self.fallback))
        except (TypeError, ValueError):
            return self.fallback

    def __int__(self) -> int:
        return self.value()

    def __index__(self) -> int:
        return self.value()

    def __eq__(self, other: object) -> bool:
        try:
            return self.value() == int(other)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __str__(self) -> str:
        return str(self.value())

    def __repr__(self) -> str:
        return f"_LiveTickSequence({self.value()})"


def _signal_key(signal: Any) -> tuple[type[Any], str]:
    return type(signal), str(getattr(signal, "signal_id", "") or "")


def _prune_delivery_state() -> None:
    cutoff = time.monotonic() - _DELIVERY_STATE_TTL_SECONDS
    stale = [
        key
        for key, state in _DELIVERY_STATE.items()
        if float(state.get("updated_monotonic", 0.0) or 0.0) < cutoff
    ]
    for key in stale:
        _DELIVERY_STATE.pop(key, None)


def _state_for(signal: Any) -> dict[str, Any]:
    _prune_delivery_state()
    key = _signal_key(signal)
    now = time.monotonic()
    state = _DELIVERY_STATE.setdefault(
        key,
        {
            "first_seen_monotonic": now,
            "original_generated_monotonic": float(
                getattr(signal, "generated_monotonic", now) or now
            ),
            "queued_monotonic": now,
            "refresh_count": 0,
        },
    )
    state["updated_monotonic"] = now
    return state


def _mark_queued(signal: Any) -> None:
    state = _state_for(signal)
    state.setdefault("queued_monotonic", time.monotonic())
    state["updated_monotonic"] = time.monotonic()


def _current_tick(bot: RFDir5TradingBot, signal: Any) -> dict[str, Any] | None:
    return standardized._current_tick(
        bot,
        str(getattr(signal, "symbol", "") or ""),
    )


def _apply_tick(
    bot: RFDir5TradingBot,
    signal: Any,
    tick: dict[str, Any],
    *,
    live_sequence: bool,
) -> int:
    symbol = str(getattr(signal, "symbol", "") or "")
    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    sequence = int(tick.get("tick_sequence") or 0)
    signal.reference_entry_quote = quote
    signal.signal_tick_epoch = epoch
    signal.signal_tick_id = bot._tick_identity(symbol, epoch, quote)
    signal.tick_sequence = (
        _LiveTickSequence(bot, symbol, sequence) if live_sequence else sequence
    )
    signal.generated_monotonic = time.monotonic()
    signal.generated_at = standardized._now_iso()
    return sequence


def refresh_signal_for_delivery(bot: RFDir5TradingBot, signal: Any) -> bool:
    """Keep a qualified cycle executable until it reaches private transport.

    Signal qualification is frozen for the duration of one atomic standardized
    cycle. Internal proposal latency, account database work, another account group,
    or a normal market tick must never convert that qualified decision into a skip.
    A missing current public tick is still a genuine transport blocker.
    """

    cycle_id = signal_metadata.standardized_cycle_id(signal)
    if not cycle_id:
        return False
    tick = _current_tick(bot, signal)
    if tick is None:
        bot.logger.error(
            "STANDARDIZED_SIGNAL_DELIVERY_BLOCKED signal_id=%s cycle_id=%s "
            "reason=current_market_tick_unavailable",
            str(getattr(signal, "signal_id", "") or ""),
            cycle_id,
        )
        return False

    state = _state_for(signal)
    now = time.monotonic()
    original_generated = float(state.get("original_generated_monotonic", now) or now)
    queued = float(state.get("queued_monotonic", original_generated) or original_generated)
    state["refresh_count"] = int(state.get("refresh_count", 0) or 0) + 1
    state["cycle_id"] = cycle_id
    state["updated_monotonic"] = now
    sequence = _apply_tick(bot, signal, tick, live_sequence=True)
    bot.logger.info(
        "STANDARDIZED_SIGNAL_PINNED signal_id=%s cycle_id=%s symbol=%s "
        "contract_type=%s barrier=%s signal_age_ms=%.1f queue_delay_ms=%.1f "
        "current_tick_sequence=%s refresh_count=%s "
        "internal_expiry=false delivery_policy=qualified_cycle_must_reach_transport",
        str(getattr(signal, "signal_id", "") or ""),
        cycle_id,
        str(getattr(signal, "symbol", "") or ""),
        str(getattr(signal, "contract_type", "") or ""),
        str(getattr(signal, "barrier", "") or ""),
        max(0.0, (now - original_generated) * 1000.0),
        max(0.0, (now - queued) * 1000.0),
        sequence,
        state["refresh_count"],
    )
    return True


def _finalize_private_boundary(bot: RFDir5TradingBot, signal: Any) -> bool:
    tick = _current_tick(bot, signal)
    cycle_id = signal_metadata.standardized_cycle_id(signal)
    if not cycle_id or tick is None:
        bot.logger.error(
            "STANDARDIZED_PRIVATE_BOUNDARY_BLOCKED signal_id=%s cycle_id=%s "
            "reason=current_market_tick_unavailable",
            str(getattr(signal, "signal_id", "") or ""),
            cycle_id or "missing",
        )
        return False
    state = _state_for(signal)
    now = time.monotonic()
    state["transport_started_monotonic"] = now
    state["updated_monotonic"] = now
    sequence = _apply_tick(bot, signal, tick, live_sequence=False)
    original_generated = float(state.get("original_generated_monotonic", now) or now)
    queued = float(state.get("queued_monotonic", original_generated) or original_generated)
    bot.logger.warning(
        "STANDARDIZED_PRIVATE_BOUNDARY signal_id=%s cycle_id=%s symbol=%s "
        "contract_type=%s barrier=%s tick_sequence=%s signal_age_ms=%.1f "
        "queue_delay_ms=%.1f private_transport_now=true",
        str(getattr(signal, "signal_id", "") or ""),
        cycle_id,
        str(getattr(signal, "symbol", "") or ""),
        str(getattr(signal, "contract_type", "") or ""),
        str(getattr(signal, "barrier", "") or ""),
        sequence,
        max(0.0, (now - original_generated) * 1000.0),
        max(0.0, (now - queued) * 1000.0),
    )
    return True


def _role_matches_account(route: Any, account_route: Any) -> bool:
    if account_route.selection.family != route.family:
        return False
    if account_route.selection.side != route.side:
        return False
    role = str(getattr(route, "role", "") or "").upper()
    mode = str(getattr(account_route, "mode", "") or "").upper()
    if role == "SHARED":
        return True
    if role == "NORMAL":
        return mode == "NORMAL_MODE"
    if role == "RECOVERY":
        return mode == "RECOVERY_PENDING" and int(account_route.split_remaining) <= 0
    if role == "POST_VIRTUAL":
        return mode == "RECOVERY_PENDING" and int(account_route.split_remaining) > 0
    if role == "VIRTUAL":
        return mode == "VIRTUAL_MODE"
    return True


def _refresh_manual_scope(bot: RFDir5TradingBot, signal: Any) -> None:
    route = getattr(bot, "_multi_strategy_signal_routes", {}).get(
        str(getattr(signal, "signal_id", "") or "")
    )
    if route is None:
        return
    before = {int(value) for value in set(getattr(route, "scope_ids", set()) or set())}
    current = {
        int(account_route.managed_id)
        for account_route in multi._strategy_snapshot(bot, force=True)
        if _role_matches_account(route, account_route)
    }
    route.scope_ids = set(current)
    joined = sorted(current - before)
    removed = sorted(before - current)
    if joined or removed:
        bot.logger.warning(
            "STANDARDIZED_SCOPE_REFRESHED signal_id=%s family=%s side=%s role=%s "
            "previous_accounts=%s current_accounts=%s joined_accounts=%s "
            "removed_accounts=%s new_accounts_join_current_cycle=true",
            str(getattr(signal, "signal_id", "") or ""),
            str(getattr(route, "family", "") or ""),
            str(getattr(route, "side", "") or ""),
            str(getattr(route, "role", "") or ""),
            len(before),
            len(current),
            joined,
            removed,
        )


def _live_aidr_scope(bot: RFDir5TradingBot, signal: Any) -> set[int]:
    normal, first_recovery, post_virtual, virtual = aidr._account_recovery_groups(bot)
    try:
        barrier = int(str(getattr(signal, "barrier", "") or "-1"))
    except (TypeError, ValueError):
        barrier = -1
    if barrier == int(aidr.NORMAL_BARRIER):
        return set(normal)
    if barrier == int(aidr.RECOVERY_BARRIER):
        return set(first_recovery)
    if barrier == int(aidr.POST_VIRTUAL_BARRIER):
        return set(post_virtual) | set(virtual)
    return set()


async def _drain_multi_strategy(bot: RFDir5TradingBot) -> None:
    while getattr(bot, "_multi_strategy_candidates", {}):
        await standardized._standardized_multi_strategy_arbitrate(bot)
        await asyncio.sleep(0)


def _ensure_multi_task(bot: RFDir5TradingBot) -> None:
    task = getattr(bot, "_multi_strategy_task", None)
    if task is not None and not task.done():
        return
    if not getattr(bot, "_multi_strategy_candidates", {}):
        return
    task = asyncio.create_task(
        _drain_multi_strategy(bot),
        name="guaranteed_multi_strategy_delivery",
    )
    bot._multi_strategy_task = task

    def finished(done: asyncio.Task[Any]) -> None:
        if getattr(bot, "_multi_strategy_task", None) is done:
            bot._multi_strategy_task = None
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            bot.logger.exception("GUARANTEED_MULTI_STRATEGY_DELIVERY_FAILED")
        if getattr(bot, "_multi_strategy_candidates", {}):
            asyncio.get_running_loop().call_soon(_ensure_multi_task, bot)

    task.add_done_callback(finished)


def _queue_multi_strategy(bot: RFDir5TradingBot, signal: Any) -> None:
    route = getattr(bot, "_multi_strategy_signal_routes", {}).get(
        str(getattr(signal, "signal_id", "") or "")
    )
    if route is None or not set(getattr(route, "scope_ids", set()) or set()):
        return
    _mark_queued(signal)
    key = standardized._queue_key(route, signal)
    previous = bot._multi_strategy_candidates.get(key)
    if previous is not None:
        try:
            bot.repository.mark_signal(
                previous.signal_id,
                status="SKIP_NEWER_SAME_ACCOUNT_GROUP_SIGNAL",
            )
        except Exception:
            pass
    bot._multi_strategy_candidates[key] = signal
    _ensure_multi_task(bot)


async def _drain_aidr_candidates(bot: RFDir5TradingBot) -> None:
    while getattr(bot, "hybrid_digit_candidates", {}):
        await standardized._standardized_aidr_arbitrate(bot)
        await asyncio.sleep(0)


def install_guaranteed_signal_delivery() -> None:
    """Guarantee delivery of every qualified standardized account-group cycle."""

    global _INSTALLED
    if _INSTALLED:
        return

    # A standardized cycle is cleared explicitly after its purchase wrapper exits.
    # It must not disappear because a fixed wall-clock TTL elapsed during internal
    # proposal, database, account or private-WebSocket preparation.
    signal_metadata._METADATA_TTL_SECONDS = float("inf")
    standardized.MAX_STANDARDIZED_SIGNAL_AGE_SECONDS = float("inf")
    standardized.refresh_signal_for_execution = refresh_signal_for_delivery

    original_purchase = RFDir5TradingBot._purchase_accounts_by_stake

    async def purchase_at_current_boundary(
        self: RFDir5TradingBot,
        *,
        signal: Any,
        eligible_accounts: list[tuple[str, str]],
        stake_by_token: dict[str, float],
        pre_trade_profit_ratio: float = 0.0,
    ) -> list[dict[str, Any]]:
        if signal_metadata.standardized_cycle_id(signal):
            if not _finalize_private_boundary(self, signal):
                return []
        return await original_purchase(
            self,
            signal=signal,
            eligible_accounts=eligible_accounts,
            stake_by_token=stake_by_token,
            pre_trade_profit_ratio=pre_trade_profit_ratio,
        )

    purchase_at_current_boundary._guaranteed_signal_delivery = True  # type: ignore[attr-defined]
    RFDir5TradingBot._purchase_accounts_by_stake = purchase_at_current_boundary

    original_buy_scope = aidr._buy_for_scope

    async def buy_for_current_scope(
        bot: RFDir5TradingBot,
        signal: Any,
        economics: Any,
        managed_ids: set[int],
        *,
        recovery_enabled: bool,
    ) -> None:
        current = _live_aidr_scope(bot, signal)
        before = {int(value) for value in set(managed_ids or set())}
        joined = sorted(current - before)
        removed = sorted(before - current)
        if joined or removed:
            bot.logger.warning(
                "AIDR_SCOPE_REFRESHED signal_id=%s barrier=%s previous_accounts=%s "
                "current_accounts=%s joined_accounts=%s removed_accounts=%s "
                "new_accounts_join_current_cycle=true",
                str(getattr(signal, "signal_id", "") or ""),
                str(getattr(signal, "barrier", "") or ""),
                len(before),
                len(current),
                joined,
                removed,
            )
        await original_buy_scope(
            bot,
            signal,
            economics,
            current,
            recovery_enabled=recovery_enabled,
        )

    aidr._buy_for_scope = buy_for_current_scope

    original_buy = RFDir5TradingBot._buy_selected_accounts

    async def buy_with_live_membership(
        self: RFDir5TradingBot,
        signal: Any,
        economics: Any,
    ) -> None:
        cycle_id = signal_metadata.standardized_cycle_id(signal)
        if cycle_id:
            _mark_queued(signal)
            _refresh_manual_scope(self, signal)
        started = time.monotonic()
        try:
            await original_buy(self, signal, economics)
        finally:
            if cycle_id:
                state = _state_for(signal)
                transport_started = float(
                    state.get("transport_started_monotonic", started) or started
                )
                self.logger.warning(
                    "STANDARDIZED_DELIVERY_FINISHED signal_id=%s cycle_id=%s "
                    "total_delivery_ms=%.1f private_transport_ms=%.1f "
                    "result_check=account_cycle_receipts",
                    str(getattr(signal, "signal_id", "") or ""),
                    cycle_id,
                    max(0.0, (time.monotonic() - float(
                        state.get("original_generated_monotonic", started) or started
                    )) * 1000.0),
                    max(0.0, (time.monotonic() - transport_started) * 1000.0),
                )

    buy_with_live_membership._guaranteed_signal_membership = True  # type: ignore[attr-defined]
    RFDir5TradingBot._buy_selected_accounts = buy_with_live_membership

    # Signals arriving while a previous arbitration task is running remain in the
    # latest-per-group dictionaries and are drained before the task exits. They no
    # longer wait for an unrelated future tick to restart arbitration.
    multi._queue_candidate = _queue_multi_strategy
    standardized._queue_standardized_candidate = _queue_multi_strategy
    hybrid._arbitrate_digits = _drain_aidr_candidates
    continuation._recovery_aware_arbitrate = _drain_aidr_candidates

    RFDir5TradingBot._guaranteed_signal_delivery_installed = True
    _INSTALLED = True
    logging.getLogger(__name__).warning(
        "GUARANTEED_SIGNAL_DELIVERY_INSTALLED version=%s internal_expiry=false "
        "live_tick_pinning=true live_scope_refresh=true queue_drain=true",
        GUARANTEED_SIGNAL_DELIVERY_VERSION,
    )
