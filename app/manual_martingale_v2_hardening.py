from __future__ import annotations

from app.recovery import ceil_cents
from app.repositories.rf_dir5_repository import RFDir5Repository


_INSTALLED = False
MINIMUM_DERIV_STAKE = 0.35


def split_recovery_stake_by_parts(
    *,
    base_stake: float,
    recovery_debt: float,
    proposal_profit_ratio: float,
    remaining_parts: int,
) -> tuple[float, float]:
    """Divide debt across parts without forcing every part back to base stake.

    Split recovery exists specifically to reduce one-shot exposure. A trader with
    a $1 base stake and a $1.20 exact recovery must therefore be allowed to use
    roughly $0.40 per part when three parts are selected, subject to Deriv's
    minimum stake. The cycle ends as soon as actual settled profit clears the debt.
    """

    del base_stake
    minimum = ceil_cents(MINIMUM_DERIV_STAKE)
    debt = max(0.0, float(recovery_debt or 0.0))
    ratio = float(proposal_profit_ratio or 0.0)
    if debt <= 0.009 or ratio <= 0:
        return minimum, minimum

    buffer = max(0.05, debt * 0.06)
    full_exact_stake = ceil_cents(max(minimum, (debt + buffer) / ratio))
    parts = max(1, min(3, int(remaining_parts or 1)))
    part_stake = (
        full_exact_stake
        if parts == 1
        else ceil_cents(max(minimum, full_exact_stake / parts))
    )
    return part_stake, full_exact_stake


def install_manual_martingale_v2_hardening() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import app.manual_martingale_v2 as manual

    manual.split_recovery_stake = split_recovery_stake_by_parts
    RFDir5Repository._manual_martingale_v2_split_floor_hardened = True
    _INSTALLED = True
