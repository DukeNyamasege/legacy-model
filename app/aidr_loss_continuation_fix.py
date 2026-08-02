from __future__ import annotations

import asyncio
from typing import Any

import app.hybrid_digit_put as hybrid
from app.ai_digit_recovery_v1 import (
    AIDR_TRIGGER_RECOVERY,
    MIN_LIVE_EDGE,
    NORMAL_BARRIER,
    RECOVERY_BARRIER,
    _account_groups,
    _buy_for_scope,
    _clone_recovery_candidate,
    _make_aidr_candidate,
    _proposal_ok,
)

_INSTALLED = False


def _is_recovery_candidate(candidate: Any) -> bool:
    barrier = str(getattr(candidate, "barrier", "") or "").strip()
    trigger = str(getattr(candidate, "trigger_name", "") or "").upper()
    direction = str(getattr(candidate, "direction", "") or "").upper()
    return barrier == str(RECOVERY_BARRIER) or trigger == AIDR_TRIGGER_RECOVERY or direction == f"OVER_{RECOVERY_BARRIER}"


def _recovery_aware_candidate(bot: Any, symbol: str, tick: dict[str, Any]) -> Any | None:
    """Generate an OVER 3 candidate when any account is waiting after a loss.

    The original AIDR generator only emitted normal OVER 1 candidates. Recovery
    candidates were cloned later from those normal candidates. That meant a lost
    account could sit forever when the OVER 1 entry filter was not passing, even
    if the OVER 3 recovery filter was ready. Recovery now has its own candidate
    generation path.
    """

    try:
        _normal_ids, real_recovery_ids, virtual_ids = _account_groups(bot)
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        bot.logger.warning("AIDR_ACCOUNT_GROUPS_UNAVAILABLE error=%s", exc)
        return _make_aidr_candidate(
            bot,
            symbol,
            tick,
            barrier=NORMAL_BARRIER,
            recovery=False,
        )

    recovery_waiting = bool(real_recovery_ids or virtual_ids)
    if recovery_waiting:
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
            "AIDR_RECOVERY_WAITING_FOR_OVER3_SIGNAL symbol=%s real_recovery_accounts=%s virtual_accounts=%s",
            symbol,
            len(real_recovery_ids),
            len(virtual_ids),
        )

    return _make_aidr_candidate(
        bot,
        symbol,
        tick,
        barrier=NORMAL_BARRIER,
        recovery=False,
    )


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

    fresh = [
        candidate
        for candidate in queued
        if bot.market_states[candidate.symbol].tick_sequence == candidate.tick_sequence
    ]
    if not fresh:
        return

    normal_ids, real_recovery_ids, virtual_ids = _account_groups(bot)
    recovery_ids = real_recovery_ids | virtual_ids
    tasks: list[Any] = []
    role_by_signal: dict[str, str] = {}

    for candidate in fresh:
        recovery_candidate = _is_recovery_candidate(candidate)
        if normal_ids and not recovery_candidate:
            role_by_signal[candidate.signal_id] = "NORMAL"
            tasks.append(_proposal_ok(bot, candidate, MIN_LIVE_EDGE))
        if recovery_ids:
            selected = candidate if recovery_candidate else _clone_recovery_candidate(candidate)
            if selected.signal_id != candidate.signal_id:
                bot.repository.record_candidate(selected)
            role_by_signal[selected.signal_id] = "RECOVERY"
            tasks.append(_proposal_ok(bot, selected, MIN_LIVE_EDGE))

    if not tasks:
        for candidate in fresh:
            bot.repository.mark_signal(candidate.signal_id, status="SKIP_NO_ENABLED_ACCOUNTS")
        return

    results = await asyncio.gather(*tasks, return_exceptions=True)
    qualified: dict[str, list[tuple[float, Any, Any]]] = {"NORMAL": [], "RECOVERY": []}
    for result in results:
        if isinstance(result, Exception):
            bot.logger.exception("AIDR_ARBITRATION_TASK_FAILED", exc_info=result)
            continue
        if result is None:
            continue
        signal, economics = result
        role = role_by_signal.get(signal.signal_id, "NORMAL")
        if bot.market_states[signal.symbol].tick_sequence != signal.tick_sequence:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_STALE_SIGNAL", stale=True)
            continue
        score = float(signal.validated_edge or 0.0) + 0.05 * float(signal.lower95 or 0.0)
        qualified[role].append((score, signal, economics))

    for role, scope_ids, recovery_enabled in (
        ("RECOVERY", recovery_ids, True),
        ("NORMAL", normal_ids, False),
    ):
        group = qualified.get(role) or []
        if not group or not scope_ids:
            continue
        group.sort(key=lambda item: (-item[0], -float(item[1].weighted_probability), item[1].symbol))
        score, selected, economics = group[0]
        for _score, other, _economics in group[1:]:
            bot.repository.mark_signal(other.signal_id, status="SKIP_MARKET_ARBITRATION")
        bot.logger.warning(
            "AIDR_DIGIT_SELECTED role=%s signal_id=%s symbol=%s type=%s barrier=%s accounts=%s weighted=%.5f break_even=%.5f edge=%.5f score=%.5f",
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
        )
        await _buy_for_scope(bot, selected, economics, scope_ids, recovery_enabled=recovery_enabled)


def install_aidr_loss_continuation_fix() -> None:
    """Install recovery-aware AIDR candidate generation and arbitration."""

    global _INSTALLED
    if _INSTALLED:
        return

    hybrid._make_digit_candidate = _recovery_aware_candidate
    hybrid._arbitrate_digits = _recovery_aware_arbitrate
    hybrid._aidr_loss_continuation_fix_installed = True
    _INSTALLED = True
