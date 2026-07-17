from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    max_iterations = 200
    epsilon = 3e-14
    tiny = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    result = d
    for iteration in range(1, max_iterations + 1):
        m2 = 2 * iteration
        aa = iteration * (b - iteration) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        result *= d * c
        aa = -(a + iteration) * (qab + iteration) * x / (
            (a + m2) * (qap + m2)
        )
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return result


def beta_cdf(x: float, alpha: float, beta: float) -> float:
    import math

    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_term = (
        math.lgamma(alpha + beta)
        - math.lgamma(alpha)
        - math.lgamma(beta)
        + alpha * math.log(x)
        + beta * math.log1p(-x)
    )
    front = math.exp(log_term)
    if x < (alpha + 1.0) / (alpha + beta + 2.0):
        return front * _beta_continued_fraction(alpha, beta, x) / alpha
    return 1.0 - front * _beta_continued_fraction(beta, alpha, 1.0 - x) / beta


def beta_quantile(probability: float, alpha: float, beta: float) -> float:
    low, high = 0.0, 1.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if beta_cdf(middle, alpha, beta) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


@dataclass(frozen=True, slots=True)
class BayesianSnapshot:
    prior_alpha: float
    prior_beta: float
    observed_wins: int
    observed_losses: int
    posterior_alpha: float
    posterior_beta: float
    posterior_mean: float
    lower_credible_bound: float
    upper_credible_bound: float
    probability_above_break_even: float
    probability_above_safety_threshold: float
    ready: bool

    def to_dict(self) -> dict:
        return asdict(self)


class BayesianProbability:
    def __init__(
        self,
        *,
        prior_alpha: float,
        prior_beta: float,
        credible_interval: float,
        minimum_completed_trades: int,
    ) -> None:
        self.prior_alpha = float(prior_alpha)
        self.prior_beta = float(prior_beta)
        self.credible_interval = float(credible_interval)
        self.minimum_completed_trades = int(minimum_completed_trades)
        self.wins = 0
        self.losses = 0

    def update(self, won: bool) -> None:
        if won:
            self.wins += 1
        else:
            self.losses += 1

    def restore(self, wins: int, losses: int) -> None:
        self.wins = max(0, int(wins))
        self.losses = max(0, int(losses))

    def snapshot(
        self, break_even_probability: float, safety_margin: float
    ) -> BayesianSnapshot:
        alpha = self.prior_alpha + self.wins
        beta = self.prior_beta + self.losses
        tail = (1.0 - self.credible_interval) / 2.0
        safety_threshold = min(1.0, break_even_probability + safety_margin)
        return BayesianSnapshot(
            prior_alpha=self.prior_alpha,
            prior_beta=self.prior_beta,
            observed_wins=self.wins,
            observed_losses=self.losses,
            posterior_alpha=alpha,
            posterior_beta=beta,
            posterior_mean=alpha / (alpha + beta),
            lower_credible_bound=beta_quantile(tail, alpha, beta),
            upper_credible_bound=beta_quantile(1.0 - tail, alpha, beta),
            probability_above_break_even=1.0
            - beta_cdf(break_even_probability, alpha, beta),
            probability_above_safety_threshold=1.0
            - beta_cdf(safety_threshold, alpha, beta),
            ready=(self.wins + self.losses) >= self.minimum_completed_trades,
        )


@dataclass(frozen=True, slots=True)
class BayesianGroupKey:
    strategy_version: str
    market: str
    direction: str
    duration_ticks: int


class KeyedBayesianProbability:
    """Independent weak-prior posteriors for settled RF-DIR5 shadow groups."""

    def __init__(
        self,
        *,
        prior_alpha: float = 0.5,
        prior_beta: float = 0.5,
        credible_interval: float = 0.95,
        minimum_completed_trades: int = 1000,
    ) -> None:
        self.prior_alpha = float(prior_alpha)
        self.prior_beta = float(prior_beta)
        self.credible_interval = float(credible_interval)
        self.minimum_completed_trades = int(minimum_completed_trades)
        self._outcomes: dict[BayesianGroupKey, tuple[int, int]] = {}

    def restore(
        self,
        key: BayesianGroupKey,
        *,
        wins: int,
        losses: int,
    ) -> None:
        self._outcomes[key] = (max(0, int(wins)), max(0, int(losses)))

    def restore_many(
        self,
        values: Iterable[tuple[BayesianGroupKey, int, int]],
    ) -> None:
        for key, wins, losses in values:
            self.restore(key, wins=wins, losses=losses)

    def update(self, key: BayesianGroupKey, won: bool) -> None:
        wins, losses = self._outcomes.get(key, (0, 0))
        self._outcomes[key] = (wins + int(won), losses + int(not won))

    def counts(self, key: BayesianGroupKey) -> tuple[int, int]:
        return self._outcomes.get(key, (0, 0))

    def snapshot(
        self,
        key: BayesianGroupKey,
        *,
        break_even_probability: float,
        safety_margin: float = 0.01,
    ) -> BayesianSnapshot:
        wins, losses = self.counts(key)
        model = BayesianProbability(
            prior_alpha=self.prior_alpha,
            prior_beta=self.prior_beta,
            credible_interval=self.credible_interval,
            minimum_completed_trades=self.minimum_completed_trades,
        )
        model.restore(wins, losses)
        return model.snapshot(break_even_probability, safety_margin)
