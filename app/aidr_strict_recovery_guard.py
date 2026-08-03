from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.ai_digit_recovery_v1 import (
    REAL_RECOVERY_PENDING,
    VIRTUAL_WINS_REQUIRED,
    VIRTUAL_WAITING_FOR_WIN,
    _read_split_remaining,
    _write_split_remaining,
)
from app.models import AccountRiskState, ManagedAccount, VirtualTrade, utc_now
from app.repositories.rf_dir5_repository import RFDir5Repository, StakePlan

_INSTALLED = False

STOPPED_STATUSES = {"stopped", "inactive", "disabled", "real_disabled"}
PAUSED_STATUSES = {
    "manual_pause",
    "take_profit",
    "stop_loss",
    "insufficient_balance",
    "purchase_insufficient_balance",
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "contract_unavailable",
    "purchase_registration_error",
}


def _runtime_base(repo: RFDir5Repository) -> Any:
    return getattr(repo, "base", repo)


def _account_lifecycle(repo: RFDir5Repository, managed_account_id: int) -> tuple[str, bool, str, str]:
    """Return lifecycle, enabled, raw status and reason for one exact row."""

    with repo.database.session() as session:
        row = session.get(ManagedAccount, int(managed_account_id))
        if row is None:
            return "missing", False, "missing", "Managed account is missing"
        enabled = bool(row.enabled)
        status = str(row.execution_status or "inactive").strip().lower()
        reason = str(row.execution_status_reason or "")
    if status in STOPPED_STATUSES:
        return "stopped", enabled, status, reason
    if not enabled or status in PAUSED_STATUSES:
        return "paused", enabled, status, reason
    return "running", enabled, status, reason


def _set_account_active(
    repo: RFDir5Repository,
    managed_account_id: int,
    status: str,
    reason: str,
) -> bool:
    """Update a running row without ever re-enabling Pause or Stop.

    Earlier recovery code wrote ``enabled=True`` directly. A late settlement could
    therefore revive an account after Stop/Reset. Only explicit Start/Resume may
    enable an account now.
    """

    with repo.database.session() as session:
        row = session.get(ManagedAccount, int(managed_account_id), with_for_update=True)
        if row is None:
            return False
        current = str(row.execution_status or "inactive").strip().lower()
        if not bool(row.enabled) or current in STOPPED_STATUSES or current in PAUSED_STATUSES:
            return False
        row.execution_status = str(status or "active")[:30]
        row.execution_status_reason = str(reason or "")[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()
        return True


def _restore_disabled_lifecycle(
    repo: RFDir5Repository,
    managed_account_id: int,
    *,
    status: str,
    reason: str,
) -> None:
    with repo.database.session() as session:
        row = session.get(ManagedAccount, int(managed_account_id), with_for_update=True)
        if row is None:
            return
        row.enabled = False
        row.execution_status = str(status or "manual_pause")[:30]
        row.execution_status_reason = str(reason or "")[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()


def _clear_split(repo: RFDir5Repository, managed_account_id: int) -> None:
    try:
        _write_split_remaining(_runtime_base(repo), int(managed_account_id), 0)
    except Exception:
        pass


def _reset_state_after_stop(repo: RFDir5Repository, managed_account_id: int) -> None:
    """Make a completed Stop/Reset win every race with a late settlement."""

    with repo.database.session() as session:
        state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
        if state is not None:
            state.trading_day = ""
            state.daily_start_balance = 0.0
            state.session_profit = 0.0
            state.consecutive_losses = 0
            state.recovery_loss_debt = 0.0
            state.recovery_pending = False
            state.recovery_attempt_active = False
            state.protection_mode = "NORMAL_MODE"
            state.virtual_observation_count = 0
            state.virtual_win_count = 0
            state.virtual_loss_count = 0
            state.current_virtual_loss_streak = 0
            state.entered_virtual_mode_at = None
            state.recovery_pending_since = None
            state.equity_high_water = 0.0
            state.updated_at = utc_now()
    _clear_split(repo, int(managed_account_id))


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


def _debt_requires_virtual(*, debt: float, consecutive_losses: int, split_remaining: int) -> bool:
    if debt <= 0.009 or split_remaining > 0:
        return False
    return consecutive_losses >= 2


def install_aidr_strict_recovery_guard() -> None:
    """Hard-enforce one OVER-3 recovery and one post-virtual OVER-4 recovery.

    Lifecycle:
      normal OVER 1 loss
      -> one real OVER 3 exact-debt recovery
      -> if it loses, virtual OVER 4 until 1 virtual win
      -> one real OVER 4 trade targeting the entire accumulated debt
      -> an OVER-4 recovery loss returns immediately to virtual mode.

    Stop/Reset is authoritative. Late contract or virtual settlements can never
    restore debt, virtual mode, or ``enabled=True`` after a hard stop.
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
        lifecycle, _enabled, status, _reason = _account_lifecycle(
            self, int(managed_account_id)
        )
        if lifecycle != "running":
            return StakePlan(
                None,
                f"account lifecycle is {status}; explicit Start/Resume is required",
            )

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
                        f"Virtual OVER-4 confirmation active: {int(state.virtual_win_count or 0)}/"
                        f"{VIRTUAL_WINS_REQUIRED} wins.",
                    )
                    return StakePlan(
                        None,
                        "AIDR virtual OVER-4 confirmation active; real money blocked",
                        is_recovery=True,
                        recovery_debt=debt,
                    )
                if _debt_requires_virtual(
                    debt=debt,
                    consecutive_losses=consecutive_losses,
                    split_remaining=split_remaining,
                ):
                    _force_virtual_mode(
                        self,
                        state,
                        reason=(
                            "Strict AIDR guard detected a failed recovery. Real contracts are blocked "
                            "until one virtual OVER-4 win."
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
            lifecycle, _enabled, _status, _reason = _account_lifecycle(
                self, int(managed_account_id)
            )
            if lifecycle != "running":
                return StakePlan(None, "account stopped or paused before recovery purchase")
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
                    "AIDR one-shot OVER-4 full-debt recovery is armed."
                    if split_remaining > 0
                    else "AIDR exact OVER-3 recovery is armed after one real loss."
                ),
            )
        return plan

    def strict_record_outcome(self: RFDir5Repository, **kwargs: Any) -> dict[str, Any]:
        managed_account_id = int(kwargs.get("managed_account_id"))
        profit = float(kwargs.get("profit") or 0.0)
        lifecycle_before, _enabled, status_before, reason_before = _account_lifecycle(
            self, managed_account_id
        )

        # A contract can settle after the trader pressed Stop. Record the provider
        # Trade settlement elsewhere, but never rebuild account recovery state.
        if lifecycle_before in {"stopped", "missing"}:
            _reset_state_after_stop(self, managed_account_id)
            return {
                "ignored_after_stop": True,
                "session_profit": 0.0,
                "consecutive_losses": 0,
                "recovery_loss_debt": 0.0,
                "recovery_pending": False,
                "recovery_attempt_active": False,
                "protection_mode": "NORMAL_MODE",
                "raw_protection_state": "NORMAL_MODE",
                "protection_state_changed": False,
            }

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

        # Stop may race with settlement. Re-check after the repository write and
        # make Stop win deterministically.
        lifecycle_after, _enabled_after, status_after, reason_after = _account_lifecycle(
            self, managed_account_id
        )
        if lifecycle_after in {"stopped", "missing"}:
            _reset_state_after_stop(self, managed_account_id)
            result.update(
                {
                    "ignored_after_stop": True,
                    "session_profit": 0.0,
                    "consecutive_losses": 0,
                    "recovery_loss_debt": 0.0,
                    "recovery_pending": False,
                    "recovery_attempt_active": False,
                    "protection_mode": "NORMAL_MODE",
                    "raw_protection_state": "NORMAL_MODE",
                }
            )
            return result

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
                            "AIDR recovery loss recorded. Waiting for one virtual OVER-4 win "
                            "before one full-debt OVER-4 recovery."
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
        else:
            raw_mode = str(result.get("raw_protection_state") or "")
            if lifecycle_after == "running":
                if profit <= 0 and raw_mode == REAL_RECOVERY_PENDING:
                    _set_account_active(
                        self,
                        managed_account_id,
                        "recovery_pending",
                        "One real loss recorded. Next qualifying trade is exact OVER-3 recovery.",
                    )
                elif raw_mode == REAL_RECOVERY_PENDING:
                    _set_account_active(
                        self,
                        managed_account_id,
                        "recovery_pending",
                        "AIDR one-shot OVER-4 full-debt recovery is armed.",
                    )
                elif raw_mode != VIRTUAL_WAITING_FOR_WIN:
                    _set_account_active(
                        self,
                        managed_account_id,
                        "active",
                        "AIDR normal OVER-1 execution active.",
                    )

        if lifecycle_before == "paused" or lifecycle_after == "paused":
            _restore_disabled_lifecycle(
                self,
                managed_account_id,
                status=status_after or status_before or "manual_pause",
                reason=reason_after or reason_before or "Auto trading paused; state preserved",
            )
        return result

    def strict_settle_virtual(self: RFDir5Repository, **kwargs: Any) -> list[dict[str, Any]]:
        symbol = str(kwargs.get("symbol") or "")
        tick_sequence = int(kwargs.get("tick_sequence") or 0)
        now = utc_now()

        # Cancel due observations belonging to accounts that were hard-stopped.
        # They remain auditable at $0 but cannot recreate virtual progress.
        with self.database.session() as session:
            due = session.scalars(
                select(VirtualTrade)
                .where(
                    VirtualTrade.run_id == self.run_id,
                    VirtualTrade.market == symbol,
                    VirtualTrade.result == "OPEN",
                    VirtualTrade.exit_tick_sequence <= tick_sequence,
                )
                .with_for_update()
            ).all()
            for trade in due:
                row = session.get(ManagedAccount, int(trade.managed_account_id))
                status = str(row.execution_status or "inactive").strip().lower() if row else "missing"
                if row is None or status in STOPPED_STATUSES:
                    trade.result = "VIRTUAL_CANCELLED_STOP"
                    trade.reason = "Virtual observation cancelled because Auto Trade was stopped/reset"
                    trade.amount_charged = 0.0
                    trade.actual_profit_loss = 0.0
                    trade.actual_payout = 0.0
                    trade.recovery_debt_change = 0.0
                    trade.settled_at = now

        settled = original_settle_virtual(self, **kwargs)
        for item in settled:
            virtual_trade_id = str(item.get("virtual_trade_id") or "")
            if not virtual_trade_id:
                continue
            with self.database.session() as session:
                trade = session.scalar(
                    select(VirtualTrade).where(
                        VirtualTrade.virtual_trade_id == virtual_trade_id
                    )
                )
                if trade is None:
                    continue
                managed_id = int(trade.managed_account_id)
                state = session.get(AccountRiskState, managed_id)
                row = session.get(ManagedAccount, managed_id)
                if state is None or row is None:
                    continue
                mode = state.protection_mode
                wins = int(state.virtual_win_count or 0)
                enabled = bool(row.enabled)
                status = str(row.execution_status or "inactive").strip().lower()
                reason = str(row.execution_status_reason or "")

            if status in STOPPED_STATUSES:
                _reset_state_after_stop(self, managed_id)
                item["ignored_after_stop"] = True
                continue

            paused = not enabled or status in PAUSED_STATUSES
            if mode == REAL_RECOVERY_PENDING:
                if _read_split_remaining(_runtime_base(self), managed_id) <= 0:
                    _write_split_remaining(_runtime_base(self), managed_id, 1)
                if not paused:
                    _set_account_active(
                        self,
                        managed_id,
                        "recovery_pending",
                        "One virtual OVER-4 win confirmed. One full-debt OVER-4 recovery is armed.",
                    )
                item.setdefault("protection", {})["split_recovery_remaining"] = 1
            elif mode == VIRTUAL_WAITING_FOR_WIN and not paused:
                _set_account_active(
                    self,
                    managed_id,
                    "virtual_protection",
                    f"Virtual OVER-4 confirmation active: {wins}/{VIRTUAL_WINS_REQUIRED} wins.",
                )

            if paused:
                _restore_disabled_lifecycle(
                    self,
                    managed_id,
                    status=status or "manual_pause",
                    reason=reason or "Auto trading paused; virtual progress preserved",
                )
        return settled

    RFDir5Repository.plan_stake = strict_plan_stake
    RFDir5Repository.record_account_outcome = strict_record_outcome
    RFDir5Repository.settle_due_virtual_trades = strict_settle_virtual
    RFDir5Repository._aidr_strict_recovery_guard_installed = True
    _INSTALLED = True
