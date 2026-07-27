from __future__ import annotations

CANONICAL_BASE_STAKE = 0.50


def canonical_fixed_profit(
    outcome: str,
    expected_profit_ratio: float,
    *,
    base_stake: float = CANONICAL_BASE_STAKE,
) -> float:
    """Return coherent account-independent P/L for one canonical model trade.

    The canonical model is a hypothetical fixed-stake sequence. Copier timing,
    account stake size and copier-specific settlement never redefine this result.
    """
    normalized = str(outcome or "").strip().upper()
    base = max(0.0, float(base_stake))
    ratio = max(0.0, float(expected_profit_ratio or 0.0))
    if normalized == "WIN":
        return round(base * ratio, 8)
    if normalized == "LOSS":
        return round(-base, 8)
    raise ValueError(f"Unsupported canonical outcome: {outcome!r}")
