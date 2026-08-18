from __future__ import annotations

import logging
from typing import Any

from app import manual_martingale_v2 as manual
from app.manual_martingale_v2 import (
    SPLIT_MODE,
    read_manual_martingale_settings,
)
from app.repositories.rf_dir5_repository import (
    RFDir5Repository,
    StakePlan,
    VIRTUAL_WAITING_FOR_WIN,
)
from app.strategy_v2_preferences import read_strategy


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_ORIGINAL_PLAN_STAKE = None
_ORIGINAL_RECORD_OUTCOME = None


def _custom_split_settings(
    repository: RFDir5Repository,
    managed_account_id: int,
) -> dict[str, Any] | None:
    try:
        selection = read_strategy(repository.database, int(managed_account_id))
        if str(selection.family or "").strip().lower() != "custom":
            return None
        settings = read_manual_martingale_settings(repository, int(managed_account_id))
    except Exception:
        return None
    if str(settings.get("mode") or "").strip().lower() != SPLIT_MODE:
        return None
    return settings


def install_custom_split_recovery_authority() -> None:
    """Make Custom Martingale Spread begin after the first actual loss.

    Actual financial debt is the classifier. Older generic lifecycle flags may not
    report ``recovery=False`` while debt exists. Virtual Hook remains an explicit
    exception: while it is observing, no real recovery purchase is armed.
    """

    global _INSTALLED, _ORIGINAL_PLAN_STAKE, _ORIGINAL_RECORD_OUTCOME
    if _INSTALLED:
        return

    _ORIGINAL_PLAN_STAKE = RFDir5Repository.plan_stake
    _ORIGINAL_RECORD_OUTCOME = RFDir5Repository.record_account_outcome

    def plan_stake(self: RFDir5Repository, *args: Any, **kwargs: Any) -> StakePlan:
        original = _ORIGINAL_PLAN_STAKE
        if original is None:
            raise RuntimeError("Spread recovery stake planner is unavailable")
        try:
            managed_id = int(kwargs.get("managed_account_id"))
        except (TypeError, ValueError):
            return original(self, *args, **kwargs)

        settings = _custom_split_settings(self, managed_id)
        if settings is not None:
            kwargs["recovery_trigger_losses"] = 1
            kwargs["maximum_recovery_balance_fraction"] = 1.0

        plan = original(self, *args, **kwargs)
        debt = max(0.0, float(plan.recovery_debt or 0.0))
        virtual_wait = False
        if settings is not None:
            try:
                snapshot = manual._account_snapshot(self, managed_id)
                debt = max(debt, float(snapshot.get("debt") or 0.0))
                virtual_wait = str(snapshot.get("mode") or "") == VIRTUAL_WAITING_FOR_WIN
            except Exception:
                pass

            # This is the earliest Custom Split wrapper. Correct the classification
            # here so every outer recovery layer and every diagnostic log sees the
            # same invariant: real debt > 0.009 means recovery unless Virtual Hook
            # is deliberately holding financial execution.
            if debt > 0.009 and not virtual_wait and not bool(plan.is_recovery):
                plan = StakePlan(
                    stake=plan.stake,
                    reason=(str(plan.reason or "ok") + "; actual debt forces recovery classification")[:240],
                    is_recovery=True,
                    recovery_debt=debt,
                    required_recovery_stake=max(
                        float(plan.required_recovery_stake or 0.0),
                        float(plan.stake or 0.0),
                    ),
                )

            LOGGER.info(
                "CUSTOM_SPREAD_STAKE_PLAN managed_id=%s parts=%s base_stake=%.2f "
                "balance=%.2f final_stake=%s recovery=%s debt=%.2f "
                "debt_classifier_authoritative=true virtual_wait=%s reason=%s",
                managed_id,
                int(settings.get("split_count") or 1),
                float(kwargs.get("requested_stake") or 0.0),
                float(kwargs.get("current_balance") or 0.0),
                "none" if plan.stake is None else f"{float(plan.stake):.2f}",
                bool(plan.is_recovery),
                debt,
                str(bool(virtual_wait)).lower(),
                str(plan.reason or "ok")[:140],
            )
        return plan

    def record_account_outcome(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        original = _ORIGINAL_RECORD_OUTCOME
        if original is None:
            raise RuntimeError("Spread recovery settlement recorder is unavailable")
        try:
            managed_id = int(kwargs.get("managed_account_id"))
        except (TypeError, ValueError):
            return original(self, *args, **kwargs)

        settings = _custom_split_settings(self, managed_id)
        if settings is not None:
            kwargs["recovery_trigger_losses"] = 1

        result = original(self, *args, **kwargs)
        if settings is not None:
            debt = max(0.0, float(result.get("recovery_loss_debt") or 0.0))
            if debt > 0.009:
                result["recovery_pending"] = True
                result["recovery_classification"] = "REAL_DEBT_IS_RECOVERY"
            LOGGER.info(
                "CUSTOM_SPREAD_SETTLEMENT managed_id=%s parts=%s profit=%.2f "
                "debt=%.2f pending=%s remaining=%s debt_classifier_authoritative=true",
                managed_id,
                int(settings.get("split_count") or 1),
                float(kwargs.get("profit") or 0.0),
                debt,
                bool(result.get("recovery_pending")),
                result.get("manual_split_remaining", "-"),
            )
        return result

    RFDir5Repository.plan_stake = plan_stake
    RFDir5Repository.record_account_outcome = record_account_outcome
    RFDir5Repository._custom_split_recovery_authority_installed = True
    RFDir5Repository._custom_split_debt_classifier = "actual_debt_gt_0_009"
    _INSTALLED = True
