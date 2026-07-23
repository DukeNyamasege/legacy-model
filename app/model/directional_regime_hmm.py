from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Sequence

REGIME_STATES = ("FALL_CONTINUATION", "CHOPPY", "RISE_REVERSAL")


@dataclass(frozen=True, slots=True)
class DirectionalHmmInference:
    ready: bool
    state: str
    probabilities: dict[str, float]
    observation_count: int

    def to_dict(self) -> dict:
        return asdict(self)


class DirectionalRegimeHmm:
    """Three-state discrete HMM for down, flat, and up price movements."""

    _EMISSION_PRIOR = (
        (0.72, 0.08, 0.20),
        (0.42, 0.16, 0.42),
        (0.20, 0.08, 0.72),
    )

    def __init__(
        self,
        *,
        minimum_observations: int,
        training_iterations: int = 12,
    ) -> None:
        self.minimum_observations = max(50, int(minimum_observations))
        self.training_iterations = max(1, int(training_iterations))
        self.observation_count = 0
        self.trained = False
        self.initial = [1.0 / 3.0] * 3
        self.transition = [
            [0.88, 0.09, 0.03],
            [0.08, 0.84, 0.08],
            [0.03, 0.09, 0.88],
        ]
        self.emission = [list(row) for row in self._EMISSION_PRIOR]
        self.posterior = list(self.initial)

    @staticmethod
    def _observation(move: Decimal | float | int) -> int:
        value = Decimal(str(move))
        if value < 0:
            return 0
        if value > 0:
            return 2
        return 1

    @staticmethod
    def _normalize(values: Sequence[float]) -> list[float]:
        total = sum(values)
        if total <= 0:
            return [1.0 / len(values)] * len(values)
        return [value / total for value in values]

    def _forward(self, observations: Sequence[int]) -> tuple[list[list[float]], list[float]]:
        alpha: list[list[float]] = []
        scales: list[float] = []
        first = [
            self.initial[state] * self.emission[state][observations[0]]
            for state in range(3)
        ]
        first_scale = sum(first) or 1.0
        alpha.append([value / first_scale for value in first])
        scales.append(first_scale)
        for observation in observations[1:]:
            row = [
                self.emission[target][observation]
                * sum(
                    alpha[-1][source] * self.transition[source][target]
                    for source in range(3)
                )
                for target in range(3)
            ]
            scale = sum(row) or 1.0
            alpha.append([value / scale for value in row])
            scales.append(scale)
        return alpha, scales

    def _backward(
        self,
        observations: Sequence[int],
        scales: Sequence[float],
    ) -> list[list[float]]:
        beta = [[0.0, 0.0, 0.0] for _ in observations]
        beta[-1] = [1.0, 1.0, 1.0]
        for index in range(len(observations) - 2, -1, -1):
            next_observation = observations[index + 1]
            next_scale = scales[index + 1] or 1.0
            beta[index] = [
                sum(
                    self.transition[source][target]
                    * self.emission[target][next_observation]
                    * beta[index + 1][target]
                    for target in range(3)
                )
                / next_scale
                for source in range(3)
            ]
        return beta

    def _canonicalize_states(self) -> None:
        fall_index = max(
            range(3),
            key=lambda state: self.emission[state][0] - self.emission[state][2],
        )
        remaining = [state for state in range(3) if state != fall_index]
        rise_index = max(
            remaining,
            key=lambda state: self.emission[state][2] - self.emission[state][0],
        )
        choppy_index = next(
            state
            for state in range(3)
            if state not in {fall_index, rise_index}
        )
        order = [fall_index, choppy_index, rise_index]
        if order == [0, 1, 2]:
            return

        old_initial = list(self.initial)
        old_transition = [list(row) for row in self.transition]
        old_emission = [list(row) for row in self.emission]
        self.initial = [old_initial[state] for state in order]
        self.transition = [
            [old_transition[source][target] for target in order]
            for source in order
        ]
        self.emission = [old_emission[state] for state in order]

    def train(self, movements: Sequence[Decimal | float | int]) -> bool:
        observations = [self._observation(move) for move in movements]
        self.observation_count = len(observations)
        if self.observation_count < self.minimum_observations:
            self.trained = False
            return False

        for _ in range(self.training_iterations):
            alpha, scales = self._forward(observations)
            beta = self._backward(observations, scales)
            gamma = [
                self._normalize(
                    [alpha[index][state] * beta[index][state] for state in range(3)]
                )
                for index in range(len(observations))
            ]
            xi = []
            for index in range(len(observations) - 1):
                next_observation = observations[index + 1]
                values = [
                    [
                        alpha[index][source]
                        * self.transition[source][target]
                        * self.emission[target][next_observation]
                        * beta[index + 1][target]
                        for target in range(3)
                    ]
                    for source in range(3)
                ]
                denominator = sum(sum(row) for row in values) or 1.0
                xi.append(
                    [
                        [value / denominator for value in row]
                        for row in values
                    ]
                )

            self.initial = list(gamma[0])
            for source in range(3):
                denominator = sum(row[source] for row in gamma[:-1]) or 1.0
                self.transition[source] = self._normalize(
                    [
                        (
                            sum(matrix[source][target] for matrix in xi)
                            + 0.5 * (0.88 if source == target else 0.06)
                        )
                        / (denominator + 0.5)
                        for target in range(3)
                    ]
                )

            for state in range(3):
                denominator = sum(row[state] for row in gamma) + 3.0
                self.emission[state] = self._normalize(
                    [
                        (
                            sum(
                                gamma[index][state]
                                for index, value in enumerate(observations)
                                if value == observation
                            )
                            + 3.0 * self._EMISSION_PRIOR[state][observation]
                        )
                        / denominator
                        for observation in range(3)
                    ]
                )

        self._canonicalize_states()
        alpha, _ = self._forward(observations)
        self.posterior = list(alpha[-1])
        self.trained = True
        return True

    def observe(self, move: Decimal | float | int) -> DirectionalHmmInference:
        if not self.trained:
            return self.inference()
        observation = self._observation(move)
        predicted = [
            sum(
                self.posterior[source] * self.transition[source][target]
                for source in range(3)
            )
            for target in range(3)
        ]
        self.posterior = self._normalize(
            [
                predicted[state] * self.emission[state][observation]
                for state in range(3)
            ]
        )
        self.observation_count += 1
        return self.inference()

    def inference(self) -> DirectionalHmmInference:
        if not self.trained:
            return DirectionalHmmInference(
                ready=False,
                state="NOT_READY",
                probabilities={state: 0.0 for state in REGIME_STATES},
                observation_count=self.observation_count,
            )
        probabilities = dict(zip(REGIME_STATES, self.posterior, strict=True))
        return DirectionalHmmInference(
            ready=True,
            state=max(probabilities, key=probabilities.get),
            probabilities=probabilities,
            observation_count=self.observation_count,
        )
