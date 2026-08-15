from __future__ import annotations

import logging
from typing import Any

from app import manual_martingale_v2 as manual
from app.recovery import ceil_cents
from app.repositories.rf_dir5_repository import RFDir5Repository, StakePlan
from app.rf_dir5_bot import RFDir5TradingBot


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_ORIGINAL_PLAN_STAKE: Any = None
_DEFAULT_MAX_RECOVERY_BALANCE_FRACTION = 0.10
_DEFAULT_MINIMUM_BALANCE_RESERVE = 0.50


def _default_recovery_cap(*, current_balance: float, base_stake: float) -> float:
    """Use the same safety defaults as the canonical manual recovery planner."""

    return manual._safety_cap(
        current_balance=float(current_balance or 0.0),
        base_stake=ceil_cents(max(0.35, float(base_stake or 0.0))),
        maximum_recovery_balance_fraction=_DEFAULT_MAX_RECOVERY_BALANCE_FRACTION,
        minimum_balance_reserve=_DEFAULT_MINIMUM_BALANCE_RESERVE,
    )


def install_custom_split_cap_defaults_authority() -> None:
    """Repair omitted optional cap kwargs without weakening an explicit user cap.

    AccountExecutionSession intentionally calls the generic stake planner without
    optional recovery-cap kwargs. The equal-spread wrapper must therefore inherit
    the canonical 10% / $0.50 defaults rather than interpreting omission as 0%.
    Explicit cap values remain authoritative and are never overridden here.
    """

    global _INSTALLED, _ORIGINAL_PLAN_STAKE
    if _INSTALLED:
        return

    _ORIGINAL_PLAN_STAKE = RFDir5Repository.plan_stake

    def plan_with_canonical_split_cap_defaults(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> StakePlan:
        original = _ORIGINAL_PLAN_STAKE
        if original is None:
            raise RuntimeError("Stake planner is unavailable")

        plan = original(self, *args, **kwargs)
        if plan.stake is not None:
            return plan

        reason = str(plan.reason or "")
        if "equal split recovery stake" not in reason.lower():
            return plan

        # If a caller explicitly supplied either safety input, its rejection is a
        # deliberate policy result. Only repair the compatibility case where the
        # generic execution session omitted both optional values.
        if (
            "maximum_recovery_balance_fraction" in kwargs
            or "minimum_balance_reserve" in kwargs
        ):
            return plan

        try:
            managed_id = int(kwargs.get("managed_account_id"))
            settings = manual.read_manual_martingale_settings(self, managed_id)
            family = manual._manual_family(self, managed_id)
            required = float(plan.required_recovery_stake or 0.0)
            balance = float(kwargs.get("current_balance") or 0.0)
            base_stake = max(
                0.35,
                float(kwargs.get("minimum_stake") or 0.0),
                float(kwargs.get("requested_stake") or 0.0),
            )
        except (TypeError, ValueError):
            return plan

        if family == "system" or str(settings.get("mode")) != manual.SPLIT_MODE:
            return plan
        if required <= 0:
            return plan

        cap = _default_recovery_cap(
            current_balance=balance,
            base_stake=base_stake,
        )
        if required > cap + 1e-9:
            return StakePlan(
                None,
                (
                    f"equal split recovery stake {required:.2f} exceeds account "
                    f"safety cap {cap:.2f}; debt retained"
                ),
                is_recovery=True,
                recovery_debt=float(plan.recovery_debt or 0.0),
                required_recovery_stake=required,
            )

        manual._mark_recovery_attempt(self, managed_id)
        LOGGER.warning(
            "CUSTOM_SPLIT_CAP_DEFAULT_REPAIRED managed_id=%s required_stake=%.2f "
            "safety_cap=%.2f max_balance_fraction=%.2f reserve=%.2f "
            "trade_skipped=false",
            managed_id,
            required,
            cap,
            _DEFAULT_MAX_RECOVERY_BALANCE_FRACTION,
            _DEFAULT_MINIMUM_BALANCE_RESERVE,
        )
        return StakePlan(
            stake=required,
            reason=(
                f"equal split recovery accepted under canonical safety defaults; "
                f"stake {required:.2f} within cap {cap:.2f}"
            ),
            is_recovery=True,
            recovery_debt=float(plan.recovery_debt or 0.0),
            required_recovery_stake=required,
        )

    RFDir5Repository.plan_stake = plan_with_canonical_split_cap_defaults  # type: ignore[method-assign]
    RFDir5TradingBot._custom_split_cap_defaults_authority_installed = True
    RFDir5TradingBot._custom_split_cap_policy = "canonical_10pct_cap_when_optional_kwargs_omitted"
    _INSTALLED = True
