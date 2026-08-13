from __future__ import annotations

import logging
from typing import Any

from app.manual_martingale_v2 import (
    SPLIT_MODE,
    read_manual_martingale_settings,
)
from app.repositories.rf_dir5_repository import RFDir5Repository
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

    The existing split engine already divides exact recovery debt across one to
    three *successful* recovery parts and keeps failed parts outstanding. This
    final Custom-only authority aligns its activation with the Builder semantics:
    one actual loss opens recovery debt immediately, and a valid split stake may
    use the account's available spendable balance rather than the unrelated old
    System 10%-of-balance cap.

    System Strategy and existing multiplier behavior are left untouched.
    """

    global _INSTALLED, _ORIGINAL_PLAN_STAKE, _ORIGINAL_RECORD_OUTCOME
    if _INSTALLED:
        return

    _ORIGINAL_PLAN_STAKE = RFDir5Repository.plan_stake
    _ORIGINAL_RECORD_OUTCOME = RFDir5Repository.record_account_outcome

    def plan_stake(self: RFDir5Repository, *args: Any, **kwargs: Any):
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
        if settings is not None:
            LOGGER.info(
                "CUSTOM_SPREAD_STAKE_PLAN managed_id=%s parts=%s base_stake=%.2f "
                "balance=%.2f final_stake=%s recovery=%s debt=%.2f reason=%s",
                managed_id,
                int(settings.get("split_count") or 1),
                float(kwargs.get("requested_stake") or 0.0),
                float(kwargs.get("current_balance") or 0.0),
                "none" if plan.stake is None else f"{float(plan.stake):.2f}",
                bool(plan.is_recovery),
                float(plan.recovery_debt or 0.0),
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
            LOGGER.info(
                "CUSTOM_SPREAD_SETTLEMENT managed_id=%s parts=%s profit=%.2f "
                "debt=%.2f pending=%s remaining=%s",
                managed_id,
                int(settings.get("split_count") or 1),
                float(kwargs.get("profit") or 0.0),
                float(result.get("recovery_loss_debt") or 0.0),
                bool(result.get("recovery_pending")),
                result.get("manual_split_remaining", "-"),
            )
        return result

    RFDir5Repository.plan_stake = plan_stake
    RFDir5Repository.record_account_outcome = record_account_outcome
    RFDir5Repository._custom_split_recovery_authority_installed = True
    _INSTALLED = True
