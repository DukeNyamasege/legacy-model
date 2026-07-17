from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from app.model.bayesian_probability import BayesianSnapshot
from app.model.hmm_regime import HmmInference
from app.strategy.signal_detector import CandidateSignal


@dataclass(frozen=True, slots=True)
class ProposalEconomics:
    proposal_id: str
    stake: float
    payout: float
    potential_profit: float
    potential_loss: float
    break_even_probability: float
    predicted_win_probability: float
    expected_value: float
    expected_return_on_stake: float
    requested_monotonic: float
    received_monotonic: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class TradeDecision:
    decision_id: str
    signal_id: str
    baseline_signal_valid: bool
    signal_fresh: bool
    proposal_valid: bool
    hmm_ready: bool
    hmm_state: str
    hmm_state_probabilities: dict[str, float]
    bayesian_ready: bool
    posterior_mean: float
    posterior_edge_probability: float
    break_even_probability: float
    expected_value: float
    final_action: str
    rejection_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_proposal_economics(
    response: dict[str, Any],
    *,
    stake: float,
    predicted_probability: float,
    requested_monotonic: float,
    received_monotonic: float,
    app_markup_percentage: float = 0.0,
    commission_in_ask: bool = True,
) -> ProposalEconomics:
    proposal = response.get("proposal")
    if not isinstance(proposal, dict):
        raise ValueError("Proposal response is missing proposal data")
    proposal_id = str(proposal.get("id", "")).strip()
    if not proposal_id:
        raise ValueError("Proposal response is missing its ID")
    try:
        ask_price = float(proposal["ask_price"])
        gross_payout = float(proposal["payout"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Proposal is missing valid ask_price or payout") from exc
    if abs(ask_price - stake) > 0.011:
        raise ValueError(f"Proposal ask price {ask_price} does not match stake {stake}")
    try:
        reported_commission = float(proposal.get("commission") or 0.0)
    except (TypeError, ValueError):
        reported_commission = 0.0
    # The proposal returned by the registered application is the source of truth.
    # Application markup is normally already reflected in ask_price/payout; only add
    # commission when the provider explicitly reports it as a separate charge.
    separate_commission = 0.0 if commission_in_ask else max(0.0, reported_commission)
    actual_cost = ask_price + separate_commission
    payout = gross_payout
    potential_profit = payout - actual_cost
    potential_loss = actual_cost
    if payout <= actual_cost or potential_profit <= 0:
        raise ValueError("Proposal payout does not provide positive potential profit")
    break_even = actual_cost / payout
    expected_value = predicted_probability * potential_profit - (
        1.0 - predicted_probability
    ) * potential_loss
    return ProposalEconomics(
        proposal_id=proposal_id,
        stake=stake,
        payout=payout,
        potential_profit=potential_profit,
        potential_loss=potential_loss,
        break_even_probability=break_even,
        predicted_win_probability=predicted_probability,
        expected_value=expected_value,
        expected_return_on_stake=expected_value / stake,
        requested_monotonic=requested_monotonic,
        received_monotonic=received_monotonic,
    )


RF_ACTIONS = {
    "BUY_DEMO",
    "SHADOW_ONLY",
    "SKIP_VIRTUAL_GUARD",
    "SKIP_LOW_SCORE",
    "SKIP_STALE_SIGNAL",
    "SKIP_UNPROFITABLE_QUOTE",
    "SKIP_INSUFFICIENT_BALANCE",
    "SKIP_TRADING_LOCK",
    "SKIP_MARKET_QUARANTINED",
}


@dataclass(frozen=True, slots=True)
class RiseFallDecision:
    action: str
    reasons: tuple[str, ...]
    exploration: bool
    validated_edge: float | None


class RiseFallDecisionEngine:
    def __init__(
        self,
        *,
        minimum_score: int,
        stale_signal_after_ms: int,
        minimum_shadow_outcomes: int,
        required_edge_margin: float,
        real_gate_enabled: bool,
    ) -> None:
        self.minimum_score = int(minimum_score)
        self.stale_signal_after_ms = int(stale_signal_after_ms)
        self.minimum_shadow_outcomes = int(minimum_shadow_outcomes)
        self.required_edge_margin = float(required_edge_margin)
        self.real_gate_enabled = bool(real_gate_enabled)

    def decide(
        self,
        *,
        quality_score: int,
        signal_age_ms: float,
        proposal_age_ms: float,
        proposal_economics: ProposalEconomics,
        shadow_snapshot: BayesianSnapshot,
        execution_mode: str,
        virtual_guard_state: str,
        trading_locked: bool,
        market_quarantined: bool = False,
    ) -> RiseFallDecision:
        edge = shadow_snapshot.lower_credible_bound - proposal_economics.break_even_probability
        if market_quarantined:
            return RiseFallDecision("SKIP_MARKET_QUARANTINED", ("market_quarantined",), True, edge)
        if quality_score < self.minimum_score:
            return RiseFallDecision("SKIP_LOW_SCORE", ("directional_score",), True, edge)
        if signal_age_ms > self.stale_signal_after_ms or proposal_age_ms > self.stale_signal_after_ms:
            return RiseFallDecision("SKIP_STALE_SIGNAL", ("stale_signal_or_proposal",), True, edge)
        if proposal_economics.potential_profit <= 0:
            return RiseFallDecision("SKIP_UNPROFITABLE_QUOTE", ("non_positive_payout",), True, edge)
        if trading_locked:
            return RiseFallDecision("SKIP_TRADING_LOCK", ("open_strategy_contract",), True, edge)
        if virtual_guard_state in {"WAITING_FOR_VIRTUAL_WIN", "VIRTUAL_CONTRACT_ACTIVE"}:
            return RiseFallDecision("SKIP_VIRTUAL_GUARD", (virtual_guard_state,), True, edge)

        observations = shadow_snapshot.observed_wins + shadow_snapshot.observed_losses
        validated = (
            observations >= self.minimum_shadow_outcomes
            and edge > self.required_edge_margin
        )
        if execution_mode == "real" and self.real_gate_enabled and not validated:
            return RiseFallDecision("SHADOW_ONLY", ("real_validation_gate",), False, edge)
        return RiseFallDecision(
            "BUY_DEMO" if execution_mode == "demo" else "SHADOW_ONLY",
            ("exploration",) if not validated else ("validated_edge",),
            not validated,
            edge,
        )


class DecisionEngine:
    def __init__(
        self,
        *,
        reject_if_new_tick_arrives: bool,
        maximum_signal_age_ms: int,
        maximum_proposal_age_ms: int,
        bayesian_mode: str,
        bayesian_confidence_threshold: float,
        hmm_mode: str,
        favourable_state: str,
        favourable_state_threshold: float,
    ) -> None:
        self.reject_if_new_tick_arrives = reject_if_new_tick_arrives
        self.maximum_signal_age_ms = maximum_signal_age_ms
        self.maximum_proposal_age_ms = maximum_proposal_age_ms
        self.bayesian_mode = bayesian_mode
        self.bayesian_confidence_threshold = bayesian_confidence_threshold
        self.hmm_mode = hmm_mode
        self.favourable_state = favourable_state
        self.favourable_state_threshold = favourable_state_threshold

    def decide(
        self,
        *,
        signal: CandidateSignal,
        economics: ProposalEconomics,
        bayesian: BayesianSnapshot,
        hmm: HmmInference,
        current_tick_sequence: int,
        connection_session_id: str,
        connection_healthy: bool,
        pattern_reset_required: bool,
    ) -> TradeDecision:
        reasons: list[str] = []
        signal_age_ms = (time.monotonic() - signal.generated_monotonic) * 1000
        proposal_age_ms = (time.monotonic() - economics.received_monotonic) * 1000
        if signal.consumed:
            reasons.append("SKIP_DUPLICATE")
        if pattern_reset_required:
            reasons.append("SKIP_PATTERN_NOT_RESET")
        if signal_age_ms > self.maximum_signal_age_ms:
            reasons.append("SKIP_STALE_SIGNAL")
        elif (
            self.reject_if_new_tick_arrives
            and current_tick_sequence != signal.tick_sequence
        ):
            reasons.append("SKIP_STALE_SIGNAL")
        if connection_session_id != signal.connection_session_id or not connection_healthy:
            reasons.append("SKIP_CONNECTION_UNHEALTHY")
        if proposal_age_ms > self.maximum_proposal_age_ms:
            reasons.append("SKIP_INVALID_PROPOSAL")
        favourable_probability = hmm.probabilities.get(self.favourable_state, 0.0)
        if self.hmm_mode == "gate" and (
            not hmm.ready or favourable_probability < self.favourable_state_threshold
        ):
            reasons.append("SKIP_HMM_NOT_FAVOURABLE")
        bayesian_confidence_low = (
            bayesian.probability_above_safety_threshold
            < self.bayesian_confidence_threshold
        )
        if self.bayesian_mode == "gate" and (
            not bayesian.ready
            or (bayesian_confidence_low and economics.expected_value <= 0)
        ):
            reasons.append("SKIP_BAYESIAN_EDGE_INSUFFICIENT")
        if self.bayesian_mode == "gate" and economics.expected_value < 0:
            reasons.append("SKIP_NEGATIVE_EXPECTED_VALUE")
        final_action = reasons[0] if reasons else "PURCHASE"
        return TradeDecision(
            decision_id=str(uuid.uuid4()),
            signal_id=signal.signal_id,
            baseline_signal_valid=True,
            signal_fresh="SKIP_STALE_SIGNAL" not in reasons,
            proposal_valid="SKIP_INVALID_PROPOSAL" not in reasons,
            hmm_ready=hmm.ready,
            hmm_state=hmm.state,
            hmm_state_probabilities=hmm.probabilities,
            bayesian_ready=bayesian.ready,
            posterior_mean=bayesian.posterior_mean,
            posterior_edge_probability=bayesian.probability_above_safety_threshold,
            break_even_probability=economics.break_even_probability,
            expected_value=economics.expected_value,
            final_action=final_action,
            rejection_reasons=reasons,
        )
