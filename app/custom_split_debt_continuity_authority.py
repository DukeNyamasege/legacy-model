from __future__ import annotations

import logging
from typing import Any

from app import custom_split_equal_spread_authority as equal_split
from app import manual_martingale_v2 as manual
from app.recovery import ceil_cents
from app.repositories.rf_dir5_repository import (
    RFDir5Repository,
    StakePlan,
    VIRTUAL_WAITING_FOR_WIN,
)
from app.rf_dir5_bot import RFDir5TradingBot


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_ORIGINAL_PLAN_STAKE: Any = None


def install_custom_split_debt_continuity_authority() -> None:
    """Final Split planner: debt, not a stale lifecycle flag, owns recovery.

    A successful first Split leg can cause an older lower layer to briefly clear a
    recovery marker even though actual financial debt remains.  That made the next
    trade fall back to base stake.  This authority uses the persistent Split basis,
    remaining-success counter and actual debt as the source of truth.

    For Split N each successful leg targets the same profit share of the persistent
    loss pool. A losing recovery never consumes a successful part; the settlement
    authority rebases the enlarged loss pool across the configured N again.
    """

    global _INSTALLED, _ORIGINAL_PLAN_STAKE
    if _INSTALLED:
        return

    _ORIGINAL_PLAN_STAKE = RFDir5Repository.plan_stake

    def plan_persistent_equal_split(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> StakePlan:
        original = _ORIGINAL_PLAN_STAKE
        if original is None:
            raise RuntimeError("Stake planner is unavailable")
        plan = original(self, *args, **kwargs)

        try:
            managed_id = int(kwargs.get("managed_account_id"))
            settings = manual.read_manual_martingale_settings(self, managed_id)
            family = manual._manual_family(self, managed_id)
        except Exception:
            return plan
        if family == "system" or str(settings.get("mode")) != manual.SPLIT_MODE:
            return plan

        snapshot = manual._account_snapshot(self, managed_id)
        if not manual._account_running(snapshot):
            return plan
        if str(snapshot.get("mode") or "") == VIRTUAL_WAITING_FOR_WIN:
            return plan

        debt = max(0.0, float(snapshot.get("debt") or 0.0))
        ratio = float(kwargs.get("proposal_profit_ratio") or 0.0)
        if debt <= 0.009 or ratio <= 0:
            return plan

        split_count = max(1, min(3, int(settings.get("split_count") or 1)))
        remaining = manual._read_split_remaining(self, managed_id)
        basis = equal_split._read_basis_debt(self, managed_id)

        # Repair a stale lower lifecycle immediately.  Real outstanding debt is
        # never allowed to silently become a base-stake primary trade.
        if remaining <= 0:
            remaining = split_count
            manual._write_split_remaining(self, managed_id, remaining)
        if basis <= 0.009:
            basis = debt
            equal_split._write_basis_debt(self, managed_id, basis)
        if not manual._is_recovery_snapshot(snapshot):
            manual._arm_next_split(
                self,
                managed_id,
                remaining_parts=remaining,
                cleanup=remaining == 1 and split_count > 1,
            )
            snapshot = manual._account_snapshot(self, managed_id)
            if str(snapshot.get("mode") or "") == VIRTUAL_WAITING_FOR_WIN:
                return plan

        base_stake = ceil_cents(
            max(
                0.35,
                float(kwargs.get("minimum_stake") or 0.0),
                float(kwargs.get("requested_stake") or 0.0),
            )
        )
        stake, full_exact, target_profit = equal_split.equal_split_recovery_stake(
            base_stake=base_stake,
            recovery_basis_debt=basis,
            proposal_profit_ratio=ratio,
            split_count=split_count,
        )
        cap = manual._safety_cap(
            current_balance=float(kwargs.get("current_balance") or 0.0),
            base_stake=base_stake,
            maximum_recovery_balance_fraction=float(
                kwargs.get("maximum_recovery_balance_fraction") or 0.0
            ),
            minimum_balance_reserve=float(kwargs.get("minimum_balance_reserve") or 0.0),
        )
        if stake > cap + 1e-9:
            return StakePlan(
                None,
                (
                    f"equal Split {split_count} recovery stake {stake:.2f} exceeds "
                    f"account safety cap {cap:.2f}; debt retained"
                ),
                is_recovery=True,
                recovery_debt=debt,
                required_recovery_stake=stake,
            )

        manual._mark_recovery_attempt(self, managed_id)
        LOGGER.info(
            "CUSTOM_SPLIT_DEBT_CONTINUITY managed_id=%s basis_debt=%.2f "
            "outstanding_debt=%.2f split_count=%s remaining_successes=%s "
            "target_profit_per_leg=%.2f full_one_shot_stake=%.2f next_stake=%.2f "
            "base_fallback_forbidden=true",
            managed_id,
            basis,
            debt,
            split_count,
            remaining,
            target_profit,
            full_exact,
            stake,
        )
        return StakePlan(
            stake=stake,
            reason=(
                f"Split {split_count} equal recovery; target profit "
                f"{target_profit:.2f} for this successful leg; actual debt remains "
                f"{debt:.2f}"
            ),
            is_recovery=True,
            recovery_debt=debt,
            required_recovery_stake=stake,
        )

    RFDir5Repository.plan_stake = plan_persistent_equal_split  # type: ignore[method-assign]
    RFDir5TradingBot._custom_split_debt_continuity_authority_installed = True
    RFDir5TradingBot._custom_split_base_fallback_policy = "forbidden_while_actual_debt_exists"
    _INSTALLED = True
