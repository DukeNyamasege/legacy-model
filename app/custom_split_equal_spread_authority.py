from __future__ import annotations

import logging
from typing import Any

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
_ORIGINAL_RECORD_OUTCOME: Any = None
_BASIS_PREFIX = "custom_equal_split_basis_debt:"


def _basis_key(managed_account_id: int) -> str:
    return f"{_BASIS_PREFIX}{int(managed_account_id)}"


def _read_basis_debt(repository: RFDir5Repository, managed_account_id: int) -> float:
    base = manual._base_repository(repository)
    try:
        value = float(str(base.runtime_preference(_basis_key(managed_account_id)) or "0"))
    except (TypeError, ValueError, AttributeError):
        value = 0.0
    return max(0.0, value)


def _write_basis_debt(
    repository: RFDir5Repository,
    managed_account_id: int,
    value: float,
) -> None:
    base = manual._base_repository(repository)
    try:
        base.set_runtime_preference(
            _basis_key(managed_account_id),
            f"{max(0.0, float(value or 0.0)):.8f}",
        )
    except Exception:
        pass


def _clear_basis_debt(repository: RFDir5Repository, managed_account_id: int) -> None:
    _write_basis_debt(repository, managed_account_id, 0.0)


def equal_split_recovery_stake(
    *,
    base_stake: float,
    recovery_basis_debt: float,
    proposal_profit_ratio: float,
    split_count: int,
) -> tuple[float, float, float]:
    """Spread one loss pool equally across the configured number of recovery wins.

    The configured split count is always the divisor. It never changes from 2 to
    1 merely because one successful recovery leg has already settled. Each leg
    targets the same share of the recovery basis; the live proposal profit ratio
    converts that equal profit share into the required stake.
    """

    base = ceil_cents(max(0.35, float(base_stake or 0.0)))
    debt = max(0.0, float(recovery_basis_debt or 0.0))
    ratio = float(proposal_profit_ratio or 0.0)
    parts = max(1, min(3, int(split_count or 1)))
    if debt <= 0.009 or ratio <= 0:
        return base, base, 0.0

    full_exact_stake = ceil_cents(max(base, debt / ratio))
    target_profit_per_leg = debt / parts
    part_stake = ceil_cents(max(base, target_profit_per_leg / ratio))
    return part_stake, full_exact_stake, target_profit_per_leg


def install_custom_split_equal_spread_authority() -> None:
    """Final Custom Strategy Split authority: configured N is always the divisor."""

    global _INSTALLED, _ORIGINAL_PLAN_STAKE, _ORIGINAL_RECORD_OUTCOME
    if _INSTALLED:
        return

    _ORIGINAL_PLAN_STAKE = RFDir5Repository.plan_stake
    _ORIGINAL_RECORD_OUTCOME = RFDir5Repository.record_account_outcome

    def plan_equal_configured_split(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> StakePlan:
        original = _ORIGINAL_PLAN_STAKE
        if original is None:
            raise RuntimeError("Stake planner is unavailable")

        # Preserve every lower-layer lifecycle/virtual/transport side effect first,
        # then replace only the final Split stake when financial recovery is valid.
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
        if snapshot.get("mode") == VIRTUAL_WAITING_FOR_WIN:
            return plan
        if not manual._is_recovery_snapshot(snapshot):
            return plan

        debt = max(0.0, float(snapshot.get("debt") or 0.0))
        ratio = float(kwargs.get("proposal_profit_ratio") or 0.0)
        if debt <= 0.009 or ratio <= 0:
            return plan

        split_count = max(1, min(3, int(settings.get("split_count") or 1)))
        remaining = manual._read_split_remaining(self, managed_id)
        if remaining <= 0:
            remaining = split_count
            manual._write_split_remaining(self, managed_id, remaining)

        basis_debt = _read_basis_debt(self, managed_id)
        if basis_debt <= 0.009:
            basis_debt = debt
            _write_basis_debt(self, managed_id, basis_debt)

        base_stake = ceil_cents(
            max(
                0.35,
                float(kwargs.get("minimum_stake") or 0.0),
                float(kwargs.get("requested_stake") or 0.0),
            )
        )
        stake, full_exact_stake, target_profit = equal_split_recovery_stake(
            base_stake=base_stake,
            recovery_basis_debt=basis_debt,
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
                    f"equal split recovery stake {stake:.2f} exceeds account safety cap "
                    f"{cap:.2f}; debt retained"
                ),
                is_recovery=True,
                recovery_debt=debt,
                required_recovery_stake=stake,
            )

        manual._mark_recovery_attempt(self, managed_id)
        LOGGER.info(
            "CUSTOM_EQUAL_SPLIT_STAKE managed_id=%s outstanding_debt=%.2f basis_debt=%.2f "
            "split_count=%s remaining_successes=%s profit_ratio=%.6f "
            "target_profit_per_leg=%.2f full_one_shot_stake=%.2f next_stake=%.2f "
            "divisor=configured_split_count",
            managed_id,
            debt,
            basis_debt,
            split_count,
            remaining,
            ratio,
            target_profit,
            full_exact_stake,
            stake,
        )
        return StakePlan(
            stake=stake,
            reason=(
                f"equal split recovery: loss pool {basis_debt:.2f}; full one-shot "
                f"stake {full_exact_stake:.2f}; divided across configured Split "
                f"{split_count}; target profit {target_profit:.2f} per successful leg"
            ),
            is_recovery=True,
            recovery_debt=debt,
            required_recovery_stake=stake,
        )

    def record_equal_split_outcome(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        original = _ORIGINAL_RECORD_OUTCOME
        if original is None:
            raise RuntimeError("Settlement recorder is unavailable")

        try:
            managed_id = int(kwargs.get("managed_account_id"))
            settings = manual.read_manual_martingale_settings(self, managed_id)
            family = manual._manual_family(self, managed_id)
            before = manual._account_snapshot(self, managed_id)
            was_recovery = manual._is_recovery_snapshot(before)
            profit = float(kwargs.get("profit") or 0.0)
        except Exception:
            return original(self, *args, **kwargs)

        result = original(self, *args, **kwargs)
        if family == "system" or str(settings.get("mode")) != manual.SPLIT_MODE:
            return result
        if bool(result.get("ignored_after_stop")):
            _clear_basis_debt(self, managed_id)
            return result

        split_count = max(1, min(3, int(settings.get("split_count") or 1)))
        after = manual._account_snapshot(self, managed_id)
        debt_after = max(0.0, float(after.get("debt") or 0.0))

        # Every actual loss changes the loss pool. Rebase the pool and restart the
        # configured spread width so a failed recovery can never turn Split 2 into
        # one giant remaining recovery leg.
        if profit < 0 and debt_after > 0.009:
            _write_basis_debt(self, managed_id, debt_after)
            manual._write_split_remaining(self, managed_id, split_count)
            result.update(
                {
                    "manual_martingale_mode": manual.SPLIT_MODE,
                    "manual_split_total": split_count,
                    "manual_split_remaining": split_count,
                    "manual_split_basis_debt": round(debt_after, 2),
                    "manual_split_rebased_after_loss": bool(was_recovery),
                }
            )
            LOGGER.warning(
                "CUSTOM_EQUAL_SPLIT_REBASED_AFTER_LOSS managed_id=%s loss_pool=%.2f "
                "split_count=%s remaining_reset=%s previous_recovery=%s",
                managed_id,
                debt_after,
                split_count,
                split_count,
                str(bool(was_recovery)).lower(),
            )
            return result

        if profit > 0 and was_recovery:
            remaining = manual._read_split_remaining(self, managed_id)
            recovery_still_active = manual._is_recovery_snapshot(after)
            if debt_after <= 0.009 or remaining <= 0 or not recovery_still_active:
                _clear_basis_debt(self, managed_id)
                LOGGER.info(
                    "CUSTOM_EQUAL_SPLIT_COMPLETE managed_id=%s debt_after=%.2f "
                    "remaining=%s basis_cleared=true",
                    managed_id,
                    debt_after,
                    remaining,
                )
            else:
                basis_debt = _read_basis_debt(self, managed_id)
                if basis_debt <= 0.009:
                    # Recovery began before this authority was deployed. Rebuild a
                    # reasonable persistent basis without collapsing to one-shot.
                    basis_debt = max(debt_after, debt_after + max(0.0, profit))
                    _write_basis_debt(self, managed_id, basis_debt)
                result["manual_split_basis_debt"] = round(basis_debt, 2)
                LOGGER.info(
                    "CUSTOM_EQUAL_SPLIT_PROGRESS managed_id=%s basis_debt=%.2f "
                    "outstanding_debt=%.2f remaining_successes=%s split_count=%s "
                    "divisor_stays_configured_split_count=true",
                    managed_id,
                    basis_debt,
                    debt_after,
                    remaining,
                    split_count,
                )
        return result

    RFDir5Repository.plan_stake = plan_equal_configured_split  # type: ignore[method-assign]
    RFDir5Repository.record_account_outcome = record_equal_split_outcome  # type: ignore[method-assign]
    RFDir5TradingBot._custom_split_policy = "equal_loss_pool_across_configured_split_count"
    RFDir5TradingBot._custom_split_rebase_policy = "actual_loss_rebases_full_configured_spread"
    RFDir5TradingBot._custom_split_equal_spread_authority_installed = True
    _INSTALLED = True
