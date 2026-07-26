"""Pure recovery-stake mathematics shared by execution and reporting."""

from dataclasses import dataclass
import math


def ceil_cents(value: float) -> float:
    return math.ceil(max(0.0, float(value)) * 100.0 - 1e-9) / 100.0


@dataclass(frozen=True)
class RecoveryStakeCalculation:
    requested_stake: float
    required_recovery_stake: float
    safety_cap: float | None
    allowed: bool
    reason: str = ""


def calculate_recovery_stake(
    *,
    base_stake: float,
    recovery_debt: float,
    pre_trade_profit_ratio: float,
    minimum_stake: float,
    spendable_balance: float | None = None,
    current_balance: float | None = None,
    maximum_recovery_balance_fraction: float = 0.10,
) -> RecoveryStakeCalculation:
    """Choose a cent-rounded stake using only information known before purchase."""
    base = ceil_cents(max(float(base_stake), float(minimum_stake)))
    debt = max(0.0, float(recovery_debt))
    ratio = float(pre_trade_profit_ratio)
    if debt <= 0.01:
        allowed = spendable_balance is None or base <= spendable_balance + 1e-9
        return RecoveryStakeCalculation(
            base,
            0.0,
            spendable_balance,
            allowed,
            "" if allowed else "insufficient account balance for configured stake and reserve",
        )
    if ratio <= 0:
        return RecoveryStakeCalculation(base, 0.0, None, False, "recovery economics unavailable; debt retained")
    required = ceil_cents(debt / ratio)
    requested = max(base, required)
    cap = None
    if spendable_balance is not None and current_balance is not None:
        cap = min(
            max(0.0, float(spendable_balance)),
            max(base, max(0.0, float(current_balance)) * float(maximum_recovery_balance_fraction)),
        )
    allowed = cap is None or requested <= cap + 1e-9
    return RecoveryStakeCalculation(
        requested,
        required,
        cap,
        allowed,
        "" if allowed else "recovery stake exceeds account balance safety cap; debt retained",
    )
