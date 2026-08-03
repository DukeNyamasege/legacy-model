from __future__ import annotations

from typing import Any

import app.ai_digit_recovery_v1 as aidr
import app.aidr_loss_continuation_fix as continuation
import app.hybrid_digit_put as hybrid


_INSTALLED = False

_QUALITY = aidr.AIDR_STRATEGY_CONTRACT["quality"]
POST_VIRTUAL_TIGHTENING_FACTOR = float(
    _QUALITY.get("post_virtual_tightening_factor", 1.05)
)
POST_VIRTUAL_MINIMUM_LIVE_EDGE = float(
    _QUALITY.get("minimum_post_virtual_live_edge", 0.005)
)


def _ordinary_over_hit_rate(barrier: int) -> float:
    """Return the unfiltered probability for a uniform last digit above barrier."""

    normalized = max(0, min(9, int(barrier)))
    return (9 - normalized) / 10.0


POST_VIRTUAL_ALIGNMENT = min(
    1.0,
    _ordinary_over_hit_rate(aidr.POST_VIRTUAL_BARRIER)
    * max(1.0, POST_VIRTUAL_TIGHTENING_FACTOR),
)


def _is_post_virtual_barrier(value: Any) -> bool:
    try:
        return int(str(value).strip()) == int(aidr.POST_VIRTUAL_BARRIER)
    except (TypeError, ValueError):
        return False


def soft_alignment_for_barrier(barrier: Any, requested: float) -> float:
    """Use only a 5% tightening for virtual/full-recovery OVER-4 entries."""

    requested_rate = max(0.0, min(1.0, float(requested or 0.0)))
    if _is_post_virtual_barrier(barrier):
        return min(requested_rate, POST_VIRTUAL_ALIGNMENT)
    return requested_rate


def soft_edge_for_signal(signal: Any, requested: float) -> float:
    """Keep a small positive edge without trapping the $0 virtual sequence."""

    requested_edge = max(0.0, float(requested or 0.0))
    if _is_post_virtual_barrier(getattr(signal, "barrier", "")):
        return min(requested_edge, POST_VIRTUAL_MINIMUM_LIVE_EDGE)
    return requested_edge


def install_aidr_virtual_soft_gate() -> None:
    """Install role-specific soft gates after AIDR continuation arbitration.

    Normal OVER-1 and first-recovery OVER-3 retain their existing filters.
    Virtual OVER-4 observations and the single real OVER-4 recovery use the
    ordinary 50% OVER-4 hit rate tightened by only 5%, rather than inheriting the
    60% recovery alignment. Adaptive trap history cannot increase these gates.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_make_candidate = continuation._make_aidr_candidate
    original_proposal_ok = continuation._proposal_ok

    def make_candidate_with_soft_virtual_gate(
        bot: Any,
        symbol: str,
        tick: dict[str, Any],
        *,
        barrier: int = aidr.NORMAL_BARRIER,
        recovery: bool = False,
    ) -> Any | None:
        if not _is_post_virtual_barrier(barrier):
            return original_make_candidate(
                bot,
                symbol,
                tick,
                barrier=barrier,
                recovery=recovery,
            )

        # `_make_aidr_candidate` is synchronous, so the process-wide value is
        # restored before control returns to the event loop. Other roles retain
        # the normal 60% recovery threshold.
        previous = float(aidr.MIN_RECOVERY_HIT_RATE)
        aidr.MIN_RECOVERY_HIT_RATE = soft_alignment_for_barrier(barrier, previous)
        try:
            return original_make_candidate(
                bot,
                symbol,
                tick,
                barrier=barrier,
                recovery=recovery,
            )
        finally:
            aidr.MIN_RECOVERY_HIT_RATE = previous

    async def proposal_with_soft_virtual_edge(
        bot: Any,
        signal: Any,
        minimum_edge: float,
    ) -> Any | None:
        return await original_proposal_ok(
            bot,
            signal,
            soft_edge_for_signal(signal, minimum_edge),
        )

    # The active hybrid callbacks point to functions defined in the continuation
    # module. Those functions resolve these globals at call time, so replacing the
    # globals updates the live candidate and proposal path without changing normal
    # or first-recovery behavior.
    continuation._make_aidr_candidate = make_candidate_with_soft_virtual_gate
    continuation._proposal_ok = proposal_with_soft_virtual_edge
    continuation.AIDR_POST_VIRTUAL_ALIGNMENT = POST_VIRTUAL_ALIGNMENT
    continuation.AIDR_POST_VIRTUAL_MINIMUM_LIVE_EDGE = (
        POST_VIRTUAL_MINIMUM_LIVE_EDGE
    )

    # Keep direct AIDR callers consistent with the active continuation path.
    aidr._make_aidr_candidate = make_candidate_with_soft_virtual_gate
    aidr._proposal_ok = proposal_with_soft_virtual_edge

    hybrid._aidr_virtual_soft_gate_installed = True
    _INSTALLED = True
