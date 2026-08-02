from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select

import app.ai_digit_recovery_v1 as aidr
import app.hybrid_digit_put as hybrid
from app.ai_digit_recovery_v1 import (
    AIDR_TRIGGER_RECOVERY,
    NORMAL_BARRIER,
    RECOVERY_BARRIER,
    _account_groups,
    _buy_for_scope,
    _make_aidr_candidate,
    _proposal_ok,
)
from app.models import DirectionalSignal

_INSTALLED = False

# AIDR runs continuously on ten markets. One global entry window keeps it from
# firing several account cycles within a few seconds while still allowing 24/7
# operation. Recovery/virtual work is prioritized over new normal entries.
MINIMUM_AIDR_TRADE_INTERVAL_SECONDS = 15.0
AIDR_BASE_ALIGNMENT = 0.78
AIDR_RECOVERY_ALIGNMENT = 0.60
AIDR_MINIMUM_LIVE_EDGE = 0.025


def _is_recovery_candidate(candidate: Any) -> bool:
    barrier = str(getattr(candidate, "barrier", "") or "").strip()
    trigger = str(getattr(candidate, "trigger_name", "") or "").upper()
    direction = str(getattr(candidate, "direction", "") or "").upper()
    return (
        barrier == str(RECOVERY_BARRIER)
        or trigger == AIDR_TRIGGER_RECOVERY
        or direction == f"OVER_{RECOVERY_BARRIER}"
    )


def _recovery_aware_candidate(bot: Any, symbol: str, tick: dict[str, Any]) -> Any | None:
    """Generate only a correctly measured OVER 3 candidate during recovery.

    The previous fallback returned an OVER 1 candidate when an OVER 3 recovery
    filter was not ready. Arbitration could then clone that candidate to barrier
    3 while retaining OVER 1 probability measurements. That was both unsafe and
    a source of repeated recovery failures. When any account is in real recovery
    or virtual protection, normal entries wait and only a native OVER 3 candidate
    can continue the lifecycle.
    """

    try:
        _normal_ids, real_recovery_ids, virtual_ids = _account_groups(bot)
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        bot.logger.warning("AIDR_ACCOUNT_GROUPS_UNAVAILABLE error=%s", exc)
        return None

    if real_recovery_ids or virtual_ids:
        recovery_candidate = _make_aidr_candidate(
            bot,
            symbol,
            tick,
            barrier=RECOVERY_BARRIER,
            recovery=True,
        )
        if recovery_candidate is not None:
            bot.logger.warning(
                "AIDR_RECOVERY_CANDIDATE_READY symbol=%s barrier=%s real_recovery_accounts=%s virtual_accounts=%s",
                symbol,
                RECOVERY_BARRIER,
                len(real_recovery_ids),
                len(virtual_ids),
            )
            return recovery_candidate
        bot.logger.info(
            "AIDR_RECOVERY_WAITING_FOR_NATIVE_OVER3 symbol=%s real_recovery_accounts=%s virtual_accounts=%s",
            symbol,
            len(real_recovery_ids),
            len(virtual_ids),
        )
        return None

    return _make_aidr_candidate(
        bot,
        symbol,
        tick,
        barrier=NORMAL_BARRIER,
        recovery=False,
    )


def _repository_run_id(bot: Any) -> int:
    for owner_name in ("rf_repository", "repository"):
        owner = getattr(bot, owner_name, None)
        value = getattr(owner, "run_id", None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 1


def _ensure_directional_signal(bot: Any, signal: Any, *, role: str) -> None:
    """Create the directional_signals parent required by virtual_trades."""

    database = getattr(getattr(bot, "repository", None), "database", None)
    if database is None:
        return
    signal_id = str(getattr(signal, "signal_id", "") or "").strip()
    if not signal_id:
        return
    trigger_digits = [
        int(value)
        for value in tuple(getattr(signal, "trigger_digits", ()) or ())[-20:]
        if str(value).lstrip("-").isdigit()
    ]
    feature_values = {
        "aidr_role": str(role),
        "trigger_name": str(getattr(signal, "trigger_name", "") or ""),
        "barrier": str(getattr(signal, "barrier", "") or ""),
        "weighted_probability": float(getattr(signal, "weighted_probability", 0.0) or 0.0),
        "break_even_probability": float(getattr(signal, "break_even_probability", 0.0) or 0.0),
        "validated_edge": float(getattr(signal, "validated_edge", 0.0) or 0.0),
        "lower95": float(getattr(signal, "lower95", 0.0) or 0.0),
        "p100": float(getattr(signal, "p100", 0.0) or 0.0),
        "p500": float(getattr(signal, "p500", 0.0) or 0.0),
        "p1000": float(getattr(signal, "p1000", 0.0) or 0.0),
    }
    with database.session() as session:
        exists = session.scalar(
            select(DirectionalSignal.signal_id).where(DirectionalSignal.signal_id == signal_id)
        )
        if exists:
            return
        session.add(
            DirectionalSignal(
                signal_id=signal_id,
                run_id=_repository_run_id(bot),
                strategy_version=str(getattr(signal, "strategy_version", "AIDR") or "AIDR"),
                symbol=str(getattr(signal, "symbol", "") or ""),
                direction=str(getattr(signal, "direction", "") or ""),
                contract_type=str(getattr(signal, "contract_type", "DIGITOVER") or "DIGITOVER"),
                duration_ticks=int(getattr(signal, "duration_ticks", 1) or 1),
                signal_epoch=int(getattr(signal, "signal_tick_epoch", 0) or 0),
                signal_tick_id=str(getattr(signal, "signal_tick_id", "") or ""),
                tick_sequence=int(getattr(signal, "tick_sequence", 0) or 0),
                reference_entry_quote=float(getattr(signal, "reference_entry_quote", 0.0) or 0.0),
                analysis_quotes=[str(value) for value in trigger_digits],
                movements=[],
                feature_values=feature_values,
                quality_score=int(getattr(signal, "quality_score", 1) or 1),
                validated_edge=float(getattr(signal, "validated_edge", 0.0) or 0.0),
                selected_for_execution=True,
                execution_decision=f"AIDR_{str(role).upper()}_SELECTED",
                execution_reason="AIDR DIGITOVER parent signal registered",
            )
        )


def _minimum_interval(bot: Any) -> float:
    configured = float(
        getattr(getattr(bot.test2_config, "rf_strategy", None), "minimum_trade_interval_seconds", 0.0)
        or 0.0
    )
    return max(MINIMUM_AIDR_TRADE_INTERVAL_SECONDS, configured)


def _cadence_blocked(bot: Any, queued: list[Any]) -> bool:
    minimum = _minimum_interval(bot)
    last = float(getattr(bot, "rf_last_purchase_monotonic", 0.0) or 0.0)
    if not last:
        return False
    remaining = minimum - (time.monotonic() - last)
    if remaining <= 0:
        return False
    for candidate in queued:
        bot.repository.mark_signal(candidate.signal_id, status="SKIP_AIDR_TRADE_SPACING")
    bot.logger.info(
        "AIDR_CADENCE_GATE remaining_seconds=%.2f minimum_interval_seconds=%.2f",
        remaining,
        minimum,
    )
    return True


async def _recovery_aware_arbitrate(bot: Any) -> None:
    cfg = bot.test2_config.hybrid_strategy
    await asyncio.sleep(float(getattr(cfg, "candidate_window_ms", 75)) / 1000.0)
    queued = list(bot.hybrid_digit_candidates.values())
    bot.hybrid_digit_candidates.clear()
    if not queued:
        return

    bot._prune_stale_pending_contracts("aidr_loss_continuation_pre_proposal")
    if bot.is_trading_locked or bool(bot.pending_contracts_for_current_cycle):
        for candidate in queued:
            bot.repository.mark_signal(candidate.signal_id, status="SKIP_TRADING_LOCK")
        return
    if _cadence_blocked(bot, queued):
        return

    fresh = [
        candidate
        for candidate in queued
        if bot.market_states[candidate.symbol].tick_sequence == candidate.tick_sequence
    ]
    if not fresh:
        return

    normal_ids, real_recovery_ids, virtual_ids = _account_groups(bot)
    recovery_ids = real_recovery_ids | virtual_ids
    role = "RECOVERY" if recovery_ids else "NORMAL"
    scope_ids = recovery_ids if recovery_ids else normal_ids
    if not scope_ids:
        for candidate in fresh:
            bot.repository.mark_signal(candidate.signal_id, status="SKIP_NO_ENABLED_ACCOUNTS")
        return

    candidates = [candidate for candidate in fresh if _is_recovery_candidate(candidate)] if recovery_ids else [
        candidate for candidate in fresh if not _is_recovery_candidate(candidate)
    ]
    if not candidates:
        return

    tasks = [_proposal_ok(bot, candidate, AIDR_MINIMUM_LIVE_EDGE) for candidate in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    qualified: list[tuple[float, Any, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            bot.logger.exception("AIDR_ARBITRATION_TASK_FAILED", exc_info=result)
            continue
        if result is None:
            continue
        signal, economics = result
        if bot.market_states[signal.symbol].tick_sequence != signal.tick_sequence:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_STALE_SIGNAL", stale=True)
            continue
        score = float(signal.validated_edge or 0.0) + 0.05 * float(signal.lower95 or 0.0)
        qualified.append((score, signal, economics))

    if not qualified:
        return

    qualified.sort(key=lambda item: (-item[0], -float(item[1].weighted_probability), item[1].symbol))
    score, selected, economics = qualified[0]
    for _score, other, _economics in qualified[1:]:
        bot.repository.mark_signal(other.signal_id, status="SKIP_MARKET_ARBITRATION")

    bot.logger.warning(
        "AIDR_DIGIT_SELECTED role=%s signal_id=%s symbol=%s type=%s barrier=%s accounts=%s weighted=%.5f break_even=%.5f edge=%.5f score=%.5f cadence_seconds=%.1f",
        role,
        selected.signal_id,
        selected.symbol,
        selected.contract_type,
        selected.barrier,
        len(scope_ids),
        float(selected.weighted_probability),
        float(selected.break_even_probability or 0.0),
        float(selected.validated_edge or 0.0),
        score,
        _minimum_interval(bot),
    )

    try:
        _ensure_directional_signal(bot, selected, role=role)
        # Reserve the cadence slot before execution so simultaneous market ticks
        # cannot start a second cycle while this one is opening contracts.
        bot.rf_last_purchase_monotonic = time.monotonic()
        await _buy_for_scope(
            bot,
            selected,
            economics,
            scope_ids,
            recovery_enabled=bool(recovery_ids),
        )
    except Exception:
        bot.logger.exception(
            "AIDR_BUY_FOR_SCOPE_FAILED role=%s signal_id=%s symbol=%s barrier=%s accounts=%s",
            role,
            selected.signal_id,
            selected.symbol,
            selected.barrier,
            len(scope_ids),
        )
        bot.repository.mark_signal(selected.signal_id, status="AIDR_BUY_FOR_SCOPE_FAILED")


def install_aidr_loss_continuation_fix() -> None:
    """Install native recovery signals, stronger filters and controlled cadence."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Strengthen both entry families without changing the public strategy shape.
    aidr.MIN_BASE_HIT_RATE = AIDR_BASE_ALIGNMENT
    aidr.MIN_RECOVERY_HIT_RATE = AIDR_RECOVERY_ALIGNMENT
    aidr.MIN_LIVE_EDGE = AIDR_MINIMUM_LIVE_EDGE

    hybrid._make_digit_candidate = _recovery_aware_candidate
    hybrid._arbitrate_digits = _recovery_aware_arbitrate
    hybrid._aidr_loss_continuation_fix_installed = True
    _INSTALLED = True
