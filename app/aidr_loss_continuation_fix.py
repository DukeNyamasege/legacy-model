from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select

import app.ai_digit_recovery_v1 as aidr
import app.hybrid_digit_put as hybrid
from app.ai_digit_recovery_v1 import (
    AIDR_TRIGGER_POST_VIRTUAL,
    AIDR_TRIGGER_RECOVERY,
    NORMAL_BARRIER,
    POST_VIRTUAL_BARRIER,
    RECOVERY_BARRIER,
    _account_recovery_groups,
    _buy_for_scope,
    _make_aidr_candidate,
    _proposal_ok,
)
from app.models import DirectionalSignal

_INSTALLED = False

# One global entry window slows the 24/7 system without letting one account scope
# buy repeatedly. Normal and recovery scopes use fair role arbitration so a trader
# in virtual protection cannot stop every normal trader on the platform.
MINIMUM_AIDR_TRADE_INTERVAL_SECONDS = 15.0
AIDR_BASE_ALIGNMENT = 0.78
AIDR_RECOVERY_ALIGNMENT = 0.60
AIDR_MINIMUM_LIVE_EDGE = 0.025
NORMAL_ROLE = "NORMAL"
FIRST_RECOVERY_ROLE = "RECOVERY_OVER3"
POST_VIRTUAL_ROLE = "RECOVERY_OVER4"
ROLE_ORDER = (POST_VIRTUAL_ROLE, FIRST_RECOVERY_ROLE, NORMAL_ROLE)


def _candidate_role(candidate: Any) -> str:
    barrier = str(getattr(candidate, "barrier", "") or "").strip()
    trigger = str(getattr(candidate, "trigger_name", "") or "").upper()
    direction = str(getattr(candidate, "direction", "") or "").upper()
    if (
        barrier == str(POST_VIRTUAL_BARRIER)
        or trigger == AIDR_TRIGGER_POST_VIRTUAL
        or direction == f"OVER_{POST_VIRTUAL_BARRIER}"
    ):
        return POST_VIRTUAL_ROLE
    if (
        barrier == str(RECOVERY_BARRIER)
        or trigger == AIDR_TRIGGER_RECOVERY
        or direction == f"OVER_{RECOVERY_BARRIER}"
    ):
        return FIRST_RECOVERY_ROLE
    return NORMAL_ROLE


def _candidate_tick(candidate: Any) -> dict[str, Any]:
    return {
        "quote": getattr(candidate, "reference_entry_quote", 0.0),
        "epoch": int(getattr(candidate, "signal_tick_epoch", 0) or 0),
    }


def _recovery_aware_candidate(bot: Any, symbol: str, tick: dict[str, Any]) -> Any | None:
    """Create a native seed without globally suppressing another account role.

    Arbitration reconstructs the missing role from the same tick using its own
    barrier-specific probability measurements. Thus OVER-1 normal accounts and
    OVER-3 first-recovery and OVER-4 virtual/full-recovery accounts can all remain
    eligible, while only one role is selected per global cadence slot.
    """

    try:
        normal_ids, initial_recovery_ids, post_virtual_ids, virtual_ids = (
            _account_recovery_groups(bot)
        )
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        bot.logger.warning("AIDR_ACCOUNT_GROUPS_UNAVAILABLE error=%s", exc)
        return None

    post_virtual_scope = post_virtual_ids | virtual_ids
    post_virtual_candidate = None
    initial_recovery_candidate = None
    normal_candidate = None

    if post_virtual_scope:
        post_virtual_candidate = _make_aidr_candidate(
            bot,
            symbol,
            tick,
            barrier=POST_VIRTUAL_BARRIER,
            recovery=True,
        )
        if post_virtual_candidate is not None:
            bot.logger.warning(
                "AIDR_POST_VIRTUAL_CANDIDATE_READY symbol=%s barrier=%s full_recovery_accounts=%s virtual_accounts=%s normal_accounts=%s",
                symbol,
                POST_VIRTUAL_BARRIER,
                len(post_virtual_ids),
                len(virtual_ids),
                len(normal_ids),
            )

    if initial_recovery_ids:
        initial_recovery_candidate = _make_aidr_candidate(
            bot,
            symbol,
            tick,
            barrier=RECOVERY_BARRIER,
            recovery=True,
        )
        if initial_recovery_candidate is not None:
            bot.logger.warning(
                "AIDR_FIRST_RECOVERY_CANDIDATE_READY symbol=%s barrier=%s recovery_accounts=%s normal_accounts=%s",
                symbol,
                RECOVERY_BARRIER,
                len(initial_recovery_ids),
                len(normal_ids),
            )
        else:
            bot.logger.info(
                "AIDR_FIRST_RECOVERY_WAITING_FOR_OVER3 symbol=%s recovery_accounts=%s normal_accounts=%s",
                symbol,
                len(initial_recovery_ids),
                len(normal_ids),
            )

    if normal_ids:
        normal_candidate = _make_aidr_candidate(
            bot,
            symbol,
            tick,
            barrier=NORMAL_BARRIER,
            recovery=False,
        )

    # Post-virtual recovery receives first opportunity, then first recovery, while
    # arbitration reconstructs all other eligible roles from the same tick.
    return post_virtual_candidate or initial_recovery_candidate or normal_candidate


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


def _native_role_candidate(bot: Any, seed: Any, *, role: str) -> Any | None:
    expected_barrier = {
        NORMAL_ROLE: NORMAL_BARRIER,
        FIRST_RECOVERY_ROLE: RECOVERY_BARRIER,
        POST_VIRTUAL_ROLE: POST_VIRTUAL_BARRIER,
    }[role]
    recovery = role != NORMAL_ROLE
    if _candidate_role(seed) == role:
        return seed
    candidate = _make_aidr_candidate(
        bot,
        str(seed.symbol),
        _candidate_tick(seed),
        barrier=expected_barrier,
        recovery=recovery,
    )
    if candidate is not None:
        bot.repository.record_candidate(candidate)
    return candidate


def _selected_role(
    bot: Any,
    qualified: dict[str, list[tuple[float, Any, Any]]],
) -> str:
    ready = [role for role in ROLE_ORDER if qualified.get(role)]
    if not ready:
        return ""
    previous = str(getattr(bot, "_aidr_last_execution_role", "") or "")
    start = ROLE_ORDER.index(previous) + 1 if previous in ROLE_ORDER else 0
    for offset in range(len(ROLE_ORDER)):
        role = ROLE_ORDER[(start + offset) % len(ROLE_ORDER)]
        if role in ready:
            return role
    return ready[0]


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

    normal_ids, initial_recovery_ids, post_virtual_ids, virtual_ids = (
        _account_recovery_groups(bot)
    )
    scopes = {
        NORMAL_ROLE: normal_ids,
        FIRST_RECOVERY_ROLE: initial_recovery_ids,
        POST_VIRTUAL_ROLE: post_virtual_ids | virtual_ids,
    }
    if not any(scopes.values()):
        for candidate in fresh:
            bot.repository.mark_signal(candidate.signal_id, status="SKIP_NO_ENABLED_ACCOUNTS")
        return

    role_candidates: dict[str, list[Any]] = {role: [] for role in ROLE_ORDER}
    seen: set[tuple[str, str]] = set()
    for seed in fresh:
        for role in ROLE_ORDER:
            scope_ids = scopes[role]
            if not scope_ids:
                continue
            candidate = _native_role_candidate(bot, seed, role=role)
            if candidate is None:
                continue
            key = (role, str(candidate.symbol))
            if key in seen:
                continue
            seen.add(key)
            role_candidates[role].append(candidate)

    task_entries: list[tuple[str, Any, Any]] = []
    for role in ROLE_ORDER:
        for candidate in role_candidates[role]:
            task_entries.append(
                (role, candidate, _proposal_ok(bot, candidate, AIDR_MINIMUM_LIVE_EDGE))
            )
    if not task_entries:
        return

    results = await asyncio.gather(
        *(entry[2] for entry in task_entries),
        return_exceptions=True,
    )
    qualified: dict[str, list[tuple[float, Any, Any]]] = {
        role: [] for role in ROLE_ORDER
    }
    for (role, _candidate, _task), result in zip(task_entries, results, strict=True):
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
        qualified[role].append((score, signal, economics))

    role = _selected_role(bot, qualified)
    if not role:
        return
    scope_ids = scopes[role]
    group = qualified[role]
    group.sort(key=lambda item: (-item[0], -float(item[1].weighted_probability), item[1].symbol))
    score, selected, economics = group[0]

    for _score, other, _economics in group[1:]:
        bot.repository.mark_signal(other.signal_id, status="SKIP_MARKET_ARBITRATION")
    for other_role in (set(ROLE_ORDER) - {role}):
        for _score, other, _economics in qualified[other_role]:
            bot.repository.mark_signal(other.signal_id, status="SKIP_AIDR_ROLE_FAIRNESS")

    bot.logger.warning(
        "AIDR_DIGIT_SELECTED role=%s signal_id=%s symbol=%s type=%s barrier=%s accounts=%s weighted=%.5f break_even=%.5f edge=%.5f score=%.5f cadence_seconds=%.1f normal_accounts=%s first_recovery_accounts=%s full_recovery_accounts=%s virtual_accounts=%s fairness=round_robin",
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
        len(normal_ids),
        len(initial_recovery_ids),
        len(post_virtual_ids),
        len(virtual_ids),
    )

    try:
        _ensure_directional_signal(bot, selected, role=role)
        # Reserve the cadence slot before execution so simultaneous market ticks
        # cannot start a second cycle while this one is opening contracts.
        bot.rf_last_purchase_monotonic = time.monotonic()
        bot._aidr_last_execution_role = role
        await _buy_for_scope(
            bot,
            selected,
            economics,
            scope_ids,
            recovery_enabled=(role != NORMAL_ROLE),
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
    """Install native role signals, fair scopes, stronger filters and cadence."""

    global _INSTALLED
    if _INSTALLED:
        return

    aidr.MIN_BASE_HIT_RATE = AIDR_BASE_ALIGNMENT
    aidr.MIN_RECOVERY_HIT_RATE = AIDR_RECOVERY_ALIGNMENT
    aidr.MIN_LIVE_EDGE = AIDR_MINIMUM_LIVE_EDGE

    hybrid._make_digit_candidate = _recovery_aware_candidate
    hybrid._arbitrate_digits = _recovery_aware_arbitrate
    hybrid._aidr_loss_continuation_fix_installed = True
    _INSTALLED = True
