from __future__ import annotations

from typing import Any

from app.ai_digit_recovery_v1 import (
    AIDR_SPLIT_PREFIX,
    REAL_RECOVERY_PENDING,
    VIRTUAL_WAITING_FOR_WIN,
    _read_split_remaining,
    _write_split_remaining,
)
from app.models import AccountRiskState, utc_now
from app.recovery import ceil_cents
from app.repositories.rf_dir5_repository import RFDir5Repository, StakePlan

_INSTALLED = False


def _runtime_base(repo: RFDir5Repository) -> Any:
    return getattr(repo, "base", repo)


def _set_execution_status(repo: RFDir5Repository, managed_account_id: int, status: str, reason: str) -> None:
    base = _runtime_base(repo)
    setter = getattr(base, "set_managed_account_execution_status", None)
    if callable(setter):
        try:
            setter(int(managed_account_id), status, reason)
        except Exception:
            pass


def _clear_split(repo: RFDir5Repository, managed_account_id: int) -> None:
    try:
        _write_split_remaining(_runtime_base(repo), int(managed_account_id), 0)
    except Exception:
        pass


def _force_virtual_mode(repo: RFDir5Repository, state: AccountRiskState, *, reason: str) -> None:
    state.protection_mode = VIRTUAL_WAITING_FOR_WIN
    state.recovery_pending = True
    state.recovery_attempt_active = False
    state.entered_virtual_mode_at = state.entered_virtual_mode_at or utc_now()
    state.virtual_observation_count = 0
    state.virtual_win_count = 0
    state.virtual_loss_count = 0
    state.current_virtual_loss_streak = 0
    state.recovery_pending_since = state.recovery_pending_since or utc_now()
    state.updated_at = utc_now()
    _clear_split(repo, int(state.managed_account_id))
    _set_execution_status(
        repo,
        int(state.managed_account_id),
        "virtual_protection",
        reason,
    )


def _debt_requires_virtual(*, debt: float, base_stake: float, consecutive_losses: int, split_remaining: int) -> bool:
    if debt <= 0.009 or split_remaining > 0:
        return False
    if consecutive_losses >= 2:
        return True
    # First normal loss debt is approximately the user's base stake. A failed
    # exact recovery debt becomes base + recovery stake, normally >2x base.
    return debt > max(base_stake * 2.10, base_stake + 0.05)


def install_aidr_strict_recovery_guard() -> None:
    """Hard-enforce the intended AIDR lifecycle.

    Required lifecycle:
    OVER 1 normal -> one loss -> one real OVER 3 exact recovery attempt.
    If that recovery loses, no more real recovery escalation is allowed. The
    account must enter virtual OVER 3 until two consecutive virtual wins, then
    recover the accumulated debt using two real OVER 3 profit targets.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_plan_stake = RFDir5Repository.plan_stake
    original_record_outcome = RFDir5Repository.record_account_outcome

    def strict_plan_stake(
        self: RFDir5Repository,
        *,
        managed_account_id: int,
        account_id_masked: str = "",
        current_balance: float,
        requested_stake: float,
        proposal_profit_ratio: float,
        recovery_enabled: bool,
        recovery_trigger_losses: int,
        minimum_stake: float,
        virtual_protection_enabled: bool = True,
        maximum_recovery_balance_fraction: float = 0.10,
        minimum_balance_reserve: float = 0.50,
    ) -> StakePlan:
        base_stake = max(float(minimum_stake or 0.0), float(requested_stake or 0.0), 0.35)
        with self.database.session() as session:
            state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
            if state is not None:
                debt = round(float(state.recovery_loss_debt or 0.0), 2)
                split_remaining = _read_split_remaining(_runtime_base(self), int(managed_account_id))
                consecutive_losses = int(state.consecutive_losses or 0)
                if state.protection_mode == VIRTUAL_WAITING_FOR_WIN:
                    return StakePlan(
                        None,
                        "AIDR virtual OVER-3 confirmation active; real money blocked",
                        is_recovery=True,
                        recovery_debt=debt,
                    )
                if _debt_requires_virtual(
                    debt=debt,
                    base_stake=base_stake,
                    consecutive_losses=consecutive_losses,
                    split_remaining=split_remaining,
                ):
                    _force_virtual_mode(
                        self,
                        state,
                        reason=(
                            "Strict AIDR guard: failed exact recovery detected. "
                            "Real contracts are blocked until 2 consecutive virtual OVER-3 wins."
                        ),
                    )
                    return StakePlan(
                        None,
                        "AIDR failed recovery moved to virtual mode",
                        is_recovery=True,
                        recovery_debt=debt,
                    )

        plan = original_plan_stake(
            self,
            managed_account_id=managed_account_id,
            account_id_masked=account_id_masked,
            current_balance=current_balance,
            requested_stake=requested_stake,
            proposal_profit_ratio=proposal_profit_ratio,
            recovery_enabled=recovery_enabled,
            recovery_trigger_losses=recovery_trigger_losses,
            minimum_stake=minimum_stake,
            virtual_protection_enabled=virtual_protection_enabled,
            maximum_recovery_balance_fraction=maximum_recovery_balance_fraction,
            minimum_balance_reserve=minimum_balance_reserve,
        )

        if plan.stake is not None and bool(plan.is_recovery):
            # Mark the attempt before purchase so settlement knows whether a
            # loss is the failed exact/split recovery that must send the account
            # to virtual mode. This fixes the runaway stake escalation.
            with self.database.session() as session:
                state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
                if state is not None and state.protection_mode != VIRTUAL_WAITING_FOR_WIN:
                    state.recovery_pending = True
                    state.recovery_attempt_active = True
                    state.protection_mode = REAL_RECOVERY_PENDING
                    state.recovery_pending_since = state.recovery_pending_since or utc_now()
                    state.updated_at = utc_now()
        return plan

    def strict_record_outcome(self: RFDir5Repository, **kwargs: Any) -> dict[str, Any]:
        managed_account_id = int(kwargs.get("managed_account_id"))
        profit = float(kwargs.get("profit") or 0.0)
        previous: dict[str, Any] = {}
        with self.database.session() as session:
            state = session.get(AccountRiskState, managed_account_id)
            if state is not None:
                previous = {
                    "debt": float(state.recovery_loss_debt or 0.0),
                    "pending": bool(state.recovery_pending),
                    "attempt_active": bool(state.recovery_attempt_active),
                    "mode": state.protection_mode,
                    "split_remaining": _read_split_remaining(_runtime_base(self), managed_account_id),
                }

        result = original_record_outcome(self, **kwargs)

        # A loss while there was already recovery debt means the exact recovery
        # attempt failed. There must be no second real-money recovery attempt.
        if profit <= 0 and (
            previous.get("attempt_active")
            or previous.get("pending")
            or float(previous.get("debt") or 0.0) > 0.009
            or previous.get("mode") == REAL_RECOVERY_PENDING
        ):
            with self.database.session() as session:
                state = session.get(AccountRiskState, managed_account_id, with_for_update=True)
                if state is not None:
                    _force_virtual_mode(
                        self,
                        state,
                        reason=(
                            "Strict AIDR guard: recovery loss recorded. "
                            "Waiting for 2 consecutive virtual OVER-3 wins before real recovery resumes."
                        ),
                    )
                    result.update(
                        {
                            "recovery_pending": True,
                            "recovery_attempt_active": False,
                            "protection_mode": "VIRTUAL_MODE",
                            "raw_protection_state": VIRTUAL_WAITING_FOR_WIN,
                            "protection_state_changed": True,
                            "strict_recovery_guard": "failed_recovery_to_virtual",
                            "recovery_loss_debt": float(state.recovery_loss_debt or 0.0),
                            "split_recovery_remaining": 0,
                        }
                    )
        return result

    RFDir5Repository.plan_stake = strict_plan_stake
    RFDir5Repository.record_account_outcome = strict_record_outcome
    RFDir5Repository._aidr_strict_recovery_guard_installed = True
    _INSTALLED = True
