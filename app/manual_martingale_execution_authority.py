from __future__ import annotations

import logging
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app.manual_martingale_v2 import (
    MULTIPLIER_MODE,
    read_manual_martingale_settings,
)
from app.repositories.rf_dir5_repository import RFDir5Repository
from app.strategy_v2_preferences import read_strategy


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_ORIGINAL_PLAN_STAKE = None
_ORIGINAL_RECORD_OUTCOME = None
_ORIGINAL_FAIL_CLOSED = None


def _custom_multiplier_settings(
    repository: RFDir5Repository,
    managed_account_id: int,
) -> dict[str, Any] | None:
    """Return the saved explicit multiplier policy for one Custom Strategy account."""

    try:
        selection = read_strategy(repository.database, int(managed_account_id))
        if str(selection.family or "").strip().lower() != "custom":
            return None
        settings = read_manual_martingale_settings(repository, int(managed_account_id))
    except Exception:
        return None
    if str(settings.get("mode") or "").strip().lower() != MULTIPLIER_MODE:
        return None
    return settings


def _is_stake_policy_rejection(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    if not text:
        return False
    markers = (
        "insufficient account balance for configured stake and reserve",
        "recovery stake",
        "stake plan rejected execution",
        "debt retained",
        "exceeds account safety cap",
    )
    return any(marker in text for marker in markers)


def install_manual_martingale_execution_authority() -> None:
    """Make the user-selected Custom multiplier execute exactly after one loss.

    The Builder's Martingale field is an explicit multiplier (for example 2 means
    0.35 -> 0.70 -> 1.40 across consecutive losses). The previous direct-runtime
    path still inherited two unrelated System defaults: recovery only armed after
    two losses and recovery stake was capped at 10% of account balance. On a small
    account that made a saved x2 multiplier appear enabled while every purchase
    remained at base stake, then an eventual stake-plan rejection was misclassified
    as a WebSocket/runtime failure and could cause a reconnect loop.

    For Custom multiplier mode only:
    - one actual loss arms the very next qualifying recovery trade;
    - the saved multiplier may use available balance up to the normal cash reserve;
    - an unaffordable stake is a financial skip, never a private-WS reconnect.

    System/exact-debt modes and genuine provider/session failures remain unchanged.
    """

    global _INSTALLED, _ORIGINAL_PLAN_STAKE, _ORIGINAL_RECORD_OUTCOME, _ORIGINAL_FAIL_CLOSED
    if _INSTALLED:
        return

    _ORIGINAL_PLAN_STAKE = RFDir5Repository.plan_stake
    _ORIGINAL_RECORD_OUTCOME = RFDir5Repository.record_account_outcome
    _ORIGINAL_FAIL_CLOSED = direct_runtime._fail_closed

    def plan_stake(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ):
        original = _ORIGINAL_PLAN_STAKE
        if original is None:
            raise RuntimeError("Martingale stake planner is unavailable")

        try:
            managed_id = int(kwargs.get("managed_account_id"))
        except (TypeError, ValueError):
            return original(self, *args, **kwargs)

        settings = _custom_multiplier_settings(self, managed_id)
        if settings is not None:
            # User multiplier means recover on the next qualifying trade after the
            # first actual loss. A value of 2 therefore means x2, not "after 2 losses".
            kwargs["recovery_trigger_losses"] = 1
            # Do not silently replace the user's explicit multiplier with the old
            # 10%-of-balance System cap. The existing minimum balance reserve still
            # prevents an unaffordable purchase.
            kwargs["maximum_recovery_balance_fraction"] = 1.0

        plan = original(self, *args, **kwargs)
        if settings is not None:
            LOGGER.info(
                "CUSTOM_MARTINGALE_STAKE_PLAN managed_id=%s multiplier=%.2f "
                "base_stake=%.2f balance=%.2f final_stake=%s recovery=%s reason=%s",
                managed_id,
                float(settings.get("multiplier") or 0.0),
                float(kwargs.get("requested_stake") or 0.0),
                float(kwargs.get("current_balance") or 0.0),
                "none" if plan.stake is None else f"{float(plan.stake):.2f}",
                bool(plan.is_recovery),
                str(plan.reason or "ok")[:140],
            )
        return plan

    def record_account_outcome(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ):
        original = _ORIGINAL_RECORD_OUTCOME
        if original is None:
            raise RuntimeError("Martingale settlement recorder is unavailable")

        try:
            managed_id = int(kwargs.get("managed_account_id"))
        except (TypeError, ValueError):
            return original(self, *args, **kwargs)

        settings = _custom_multiplier_settings(self, managed_id)
        if settings is not None:
            kwargs["recovery_trigger_losses"] = 1

        result = original(self, *args, **kwargs)
        if settings is not None:
            LOGGER.info(
                "CUSTOM_MARTINGALE_SETTLEMENT managed_id=%s multiplier=%.2f profit=%.2f "
                "consecutive_losses=%s recovery_pending=%s debt=%.2f",
                managed_id,
                float(settings.get("multiplier") or 0.0),
                float(kwargs.get("profit") or 0.0),
                result.get("consecutive_losses", 0),
                bool(result.get("recovery_pending")),
                float(result.get("recovery_loss_debt") or 0.0),
            )
        return result

    def fail_closed_without_reconnecting_for_stake_policy(
        bot: Any,
        managed_id: int,
        reason: str,
        *,
        log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
    ) -> None:
        original = _ORIGINAL_FAIL_CLOSED
        if original is None:
            return
        if _is_stake_policy_rejection(reason):
            safe_reason = str(reason or "stake plan rejected")[:140]
            bot._set_account_execution_status(
                int(managed_id),
                "waiting_for_condition",
                "Trade skipped by account stake policy; Auto Trading remains active.",
            )
            bot.logger.warning(
                "CUSTOM_STAKE_POLICY_SKIP managed_id=%s reconnect=false lifecycle_stop=false "
                "reason=%s",
                int(managed_id),
                safe_reason,
            )
            return
        original(bot, int(managed_id), reason, log_event=log_event)

    RFDir5Repository.plan_stake = plan_stake
    RFDir5Repository.record_account_outcome = record_account_outcome
    direct_runtime._fail_closed = fail_closed_without_reconnecting_for_stake_policy
    RFDir5Repository._manual_martingale_execution_authority_installed = True
    _INSTALLED = True
