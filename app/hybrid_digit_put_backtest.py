from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.hybrid_runtime_config import HYBRID_RUNTIME_CONFIG


@dataclass(frozen=True, slots=True)
class ReplaySnapshot:
    index: int
    over2_p100: float
    over2_p500: float
    over2_p1000: float
    under7_p100: float
    under7_p500: float
    under7_p1000: float


def _probability(values: list[int], *, over: bool, barrier: int) -> float:
    if not values:
        return 0.0
    wins = sum(value > barrier if over else value < barrier for value in values)
    return wins / len(values)


def replay_digit_windows(digits: Iterable[int]) -> list[ReplaySnapshot]:
    """Replay historical final digits without making purchases.

    This helper intentionally does not invent historical proposal prices. It is
    suitable for testing O2/U7 distribution behaviour on extracted Deriv digits;
    live/persisted proposal records must be used separately for payout economics.
    """
    values = [int(value) for value in digits if 0 <= int(value) <= 9]
    cfg = HYBRID_RUNTIME_CONFIG
    minimum = max(cfg.windows)
    result: list[ReplaySnapshot] = []
    for index in range(minimum, len(values)):
        history = values[:index]
        w100 = history[-100:]
        w500 = history[-500:]
        w1000 = history[-1000:]
        result.append(
            ReplaySnapshot(
                index=index,
                over2_p100=_probability(w100, over=True, barrier=cfg.over_barrier),
                over2_p500=_probability(w500, over=True, barrier=cfg.over_barrier),
                over2_p1000=_probability(w1000, over=True, barrier=cfg.over_barrier),
                under7_p100=_probability(w100, over=False, barrier=cfg.under_barrier),
                under7_p500=_probability(w500, over=False, barrier=cfg.under_barrier),
                under7_p1000=_probability(w1000, over=False, barrier=cfg.under_barrier),
            )
        )
    return result
