from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

FEATURE_SCHEMA_VERSION = "test2-features-v1"


@dataclass(frozen=True, slots=True)
class TickFeatures:
    current_digit: int
    is_low: float
    low_run_length: int
    low_ratio_10: float
    low_ratio_25: float
    low_ratio_50: float
    switch_rate_25: float
    continuation_rate_25: float
    mean_reversion_rate_25: float
    digit_entropy_50: float
    uniform_deviation_50: float
    digit_frequencies_50: tuple[float, ...]

    def vector(self) -> list[float]:
        return [
            float(self.current_digit),
            self.is_low,
            float(self.low_run_length),
            self.low_ratio_10,
            self.low_ratio_25,
            self.low_ratio_50,
            self.switch_rate_25,
            self.continuation_rate_25,
            self.mean_reversion_rate_25,
            self.digit_entropy_50,
            self.uniform_deviation_50,
            *self.digit_frequencies_50,
        ]


def _ratio(values: Sequence[int], predicate) -> float:
    return sum(1 for value in values if predicate(value)) / len(values) if values else 0.0


def build_features(digits: Sequence[int]) -> TickFeatures:
    if not digits:
        raise ValueError("At least one digit is required")
    values = [int(value) for value in digits]
    classes = [value <= 4 for value in values]
    run_length = 0
    for is_low in reversed(classes):
        if not is_low:
            break
        run_length += 1

    recent_25 = classes[-25:]
    transitions = list(zip(recent_25, recent_25[1:]))
    switch_rate = (
        sum(left != right for left, right in transitions) / len(transitions)
        if transitions
        else 0.0
    )
    low_origins = [(left, right) for left, right in transitions if left]
    continuation = (
        sum(right for _, right in low_origins) / len(low_origins)
        if low_origins
        else 0.0
    )
    mean_reversion = (
        sum(not right for _, right in low_origins) / len(low_origins)
        if low_origins
        else 0.0
    )

    recent_50 = values[-50:]
    counts = Counter(recent_50)
    frequencies = tuple(counts.get(digit, 0) / len(recent_50) for digit in range(10))
    entropy = -sum(freq * math.log2(freq) for freq in frequencies if freq > 0)
    uniform_deviation = sum(abs(freq - 0.1) for freq in frequencies)
    return TickFeatures(
        current_digit=values[-1],
        is_low=float(values[-1] <= 4),
        low_run_length=run_length,
        low_ratio_10=_ratio(values[-10:], lambda value: value <= 4),
        low_ratio_25=_ratio(values[-25:], lambda value: value <= 4),
        low_ratio_50=_ratio(recent_50, lambda value: value <= 4),
        switch_rate_25=switch_rate,
        continuation_rate_25=continuation,
        mean_reversion_rate_25=mean_reversion,
        digit_entropy_50=entropy,
        uniform_deviation_50=uniform_deviation,
        digit_frequencies_50=frequencies,
    )
