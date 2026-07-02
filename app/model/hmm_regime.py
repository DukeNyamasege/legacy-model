from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Sequence

from app.model.feature_builder import TickFeatures

STATES = ("MEAN_REVERSION", "NEUTRAL_RANDOM", "CONTINUATION")


@dataclass(frozen=True, slots=True)
class HmmInference:
    ready: bool
    state: str
    probabilities: dict[str, float]
    observation_count: int

    def to_dict(self) -> dict:
        return asdict(self)


class ThreeStateHmm:
    """Small categorical HMM trained on low/high transition behaviour."""

    def __init__(self, minimum_training_ticks: int) -> None:
        self.minimum_training_ticks = int(minimum_training_ticks)
        self.observation_count = 0
        self.trained = False
        self.transition_matrix = [
            [0.70, 0.25, 0.05],
            [0.15, 0.70, 0.15],
            [0.05, 0.25, 0.70],
        ]
        self.posterior = [1 / 3, 1 / 3, 1 / 3]

    def train(self, digits: Sequence[int]) -> bool:
        self.observation_count = len(digits)
        if self.observation_count < self.minimum_training_ticks:
            self.trained = False
            return False
        low_transitions = [
            int(digits[index + 1]) <= 4
            for index in range(len(digits) - 1)
            if int(digits[index]) <= 4
        ]
        continuation = (
            sum(low_transitions) / len(low_transitions) if low_transitions else 0.5
        )
        persistence = min(0.90, max(0.55, 0.60 + abs(continuation - 0.5)))
        switch = (1.0 - persistence) / 2.0
        self.transition_matrix = [
            [persistence, 1.0 - persistence - switch, switch],
            [switch, persistence, switch],
            [switch, 1.0 - persistence - switch, persistence],
        ]
        self.posterior = [1 / 3, 1 / 3, 1 / 3]
        self.trained = True
        return True

    @staticmethod
    def _emissions(features: TickFeatures) -> list[float]:
        reversion_score = 4.0 * (features.mean_reversion_rate_25 - 0.5)
        continuation_score = 4.0 * (features.continuation_rate_25 - 0.5)
        neutral_score = 2.0 - 5.0 * abs(features.low_ratio_50 - 0.5)
        scores = [reversion_score, neutral_score, continuation_score]
        peak = max(scores)
        exp_scores = [math.exp(score - peak) for score in scores]
        total = sum(exp_scores)
        return [value / total for value in exp_scores]

    def infer(self, features: TickFeatures) -> HmmInference:
        if not self.trained:
            return HmmInference(
                ready=False,
                state="NOT_READY",
                probabilities={state: 0.0 for state in STATES},
                observation_count=self.observation_count,
            )
        predicted = [
            sum(self.posterior[source] * self.transition_matrix[source][target] for source in range(3))
            for target in range(3)
        ]
        emissions = self._emissions(features)
        unscaled = [predicted[index] * emissions[index] for index in range(3)]
        total = sum(unscaled) or 1.0
        self.posterior = [value / total for value in unscaled]
        probabilities = dict(zip(STATES, self.posterior))
        state = max(probabilities, key=probabilities.get)
        return HmmInference(
            ready=True,
            state=state,
            probabilities=probabilities,
            observation_count=self.observation_count,
        )
