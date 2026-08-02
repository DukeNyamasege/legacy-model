from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.ai_digit_recovery_v1 import (
    REAL_RECOVERY_PENDING,
    VIRTUAL_WAITING_FOR_WIN,
    _read_split_remaining,
    _write_split_remaining,
)
from app.models import AccountRiskState, ManagedAccount, utc_now
from app.repositories.rf_dir5_repository import RFDir5Repository, StakePlan

_INSTALLED = False


def _runtime_base(repo: RFDir5Repository) -> Any:
    return getattr(repo, "base", repo)


def _set_account_active(
    repo: RFDir5Repository,
    managed_account_id: int,
    status: str,
    reason: str,
) -> None:
    """Keep an account that just traded active through recovery/virtual mode."""

    with repo.database.session() as session:
        row = session.get(ManagedAccount, int(managed_account_id), with_for_update=True)
        if row is None:
            return
        row.enabled = True
        row.execution_status = str(status or "active")[:30]
        row.execution_status_reason = str(reason or "")[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()


def _clear_split(repo: RFDir5Repository, managed_account_id: int) -> None:
    try:
        _write_split_remaining(_runtime_base(repo), int(managed_account_id), 0)
    except Exception:
        pass


def _force_virtual_mode(repo: RFDir5Repository, state: AccountRiskState, *, reason: str) -> None:
    """Enter virtual mode once without erasing progress on repeated callbacks."""

    entering = state.protection_mode != VIRTUAL_WAITING_FOR_WIN
    state.protection_mode = VIRTUAL_WAITING_FOR_WIN
    state.recovery_pending = True
    state.recovery_attempt_active = False
    state.entered_virtual_mode_at = state.entered_virtual_mode_at or utc_now()
    if entering:
        state.virtual_observation_count = 0
        state.virtual_win_count = 0
        state.virtual_loss_count = 0
        state.current_virtual_loss_streak = 0
        _clear_split(repo, int(state.managed_account_id))
    state.recovery_pending_since = state.recovery_pending_since or utc_now()
    state.updated_at = utc_now()
    _set_account_active(
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
    return debt > max(base_stake * 2.10, base_stake + 0.05)


def install_aidr_strict_recovery_guard() -> None:
    """Hard-enforce one exact recovery, virtual confirmation and two-way split.

    Lifecycle:
      normal OVER 1 loss
      -> one real OVER 3 exact-debt recovery
      -> if it loses, virtual OVER 3 until 2 consecutive virtual wins
      -> two real OVER 3 profit targets, each targeting half of current debt
      -> any split-recovery loss returns immediately to virtual mode.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_plan_stake = RFDir5Repository.plan_stake
    original_record_outcome = RFDir5Repository.record_account_outcome
    original_settle_virtual = RFDir5Repository.settle_due_virtual_trades

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
                    _set_account_active(
                        self,
                        managed_account_id,
                        "virtual_protection",
                        f"Virtual OVER-3 confirmation active: {int(state.virtual_win_count or 0)}/2 consecutive wins.",
                    )
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
                            "Strict AIDR guard detected a failed recovery. Real contracts are blocked "
                            "until 2 consecutive virtual OVER-3 wins."
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
            with self.database.session() as session:
                state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
                if state is not None and state.protection_mode != VIRTUAL_WAITING_FOR_WIN:
                    state.recovery_pending = True
                    state.recovery_attempt_active = True
                    state.protection_mode = REAL_RECOVERY_PENDING
                    state.recovery_pending_since = state.recovery_pending_since or utc_now()
                    state.updated_at = utc_now()
            split_remaining = _read_split_remaining(_runtime_base(self), int(managed_account_id))
            _set_account_active(
                self,
                managed_account_id,
                "recovery_pending",
                (
                    f"AIDR split recovery active: {split_remaining} profit target(s) remaining."
                    if split_remaining > 0
                    else "AIDR exact OVER-3 recovery is armed after one real loss."
                ),
            )
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

        failed_recovery = profit <= 0 and previous.get("mode") != VIRTUAL_WAITING_FOR_WIN and (
            previous.get("attempt_active")
            or previous.get("pending")
            or float(previous.get("debt") or 0.0) > 0.009
            or previous.get("mode") == REAL_RECOVERY_PENDING
        )
        if failed_recovery:
            with self.database.session() as session:
                state = session.get(AccountRiskState, managed_account_id, with_for_update=True)
                if state is not None:
                    _force_virtual_mode(
                        self,
                        state,
                        reason=(
                            "AIDR recovery loss recorded. Waiting for 2 consecutive virtual OVER-3 wins "
                            "before two-part real recovery resumes."
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

        raw_mode = str(result.get("raw_protection_state") or "")
        if profit <= 0 and raw_mode == REAL_RECOVERY_PENDING:
            _set_account_active(
                self,
                managed_account_id,
                "recovery_pending",
                "One real loss recorded. Next qualifying trade is exact OVER-3 recovery.",
            )
        elif raw_mode == REAL_RECOVERY_PENDING:
            split_remaining = int(result.get("split_recovery_remaining") or 0)
            _set_account_active(
                self,
                managed_account_id,
                "recovery_pending",
                f"AIDR split recovery continues: {split_remaining} target(s) remaining.",
            )
        elif raw_mode != VIRTUAL_WAITING_FOR_WIN:
            _set_account_active(
                self,
                managed_account_id,
                "active",
                "AIDR normal OVER-1 execution active.",
            )
        return result

    def strict_settle_virtual(self: RFDir5Repository, **kwargs: Any) -> list[dict[str, Any]]:
        settled = original_settle_virtual(self, **kwargs)
        for item in settled:
            account_masked = str(item.get("account") or "")
            if not account_masked:
                continue
            with self.database.session() as session:
                state = session.scalar(
                    select(AccountRiskState).where(AccountRiskState.account_id_masked == account_masked)
                )
                if state is None:
                    continue
                managed_id = int(state.managed_account_id)
                mode = state.protection_mode
                wins = int(state.virtual_win_count or 0)
            if mode == REAL_RECOVERY_PENDING:
                if _read_split_remaining(_runtime_base(self), managed_id) <= 0:
                    _write_split_remaining(_runtime_base(self), managed_id, 2)
                _set_account_active(
                    self,
                    managed_id,
                    "recovery_pending",
                    "2 consecutive virtual OVER-3 wins confirmed. Two-part real recovery is armed.",
                )
                item.setdefault("protection", {})["split_recovery_remaining"] = 2
            elif mode == VIRTUAL_WAITING_FOR_WIN:
                _set_account_active(
                    self,
                    managed_id,
                    "virtual_protection",
                    f"Virtual OVER-3 confirmation active: {wins}/2 consecutive wins.",
                )
        return settled

    RFDir5Repository.plan_stake = strict_plan_stake
    RFDir5Repository.record_account_outcome = strict_record_outcome
    RFDir5Repository.settle_due_virtual_trades = strict_settle_virtual
    RFDir5Repository._aidr_strict_recovery_guard_installed = True
    _INSTALLED = True
