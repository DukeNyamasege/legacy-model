from __future__ import annotations

import socket
from typing import Any

from app.models import AccountRiskState, ManagedAccount, utc_now
from app.repositories.test2_repository import Test2Repository

PAUSED_STATUSES = {
    "manual_pause",
    "take_profit",
    "stop_loss",
    "insufficient_balance",
    "purchase_insufficient_balance",
    "credential_error",
    "invalid_account",
    "token_required",
    "real_disabled",
}
STOPPED_STATUS = "stopped"

_INSTALLED = False
_ORIGINAL_SET_STATUS = Test2Repository.set_managed_account_execution_status


def _set_enabled_preserving_state(
    self: Test2Repository,
    account_id: int,
    enabled: bool,
) -> dict[str, Any]:
    """Enable/disable execution without silently mutating recovery state.

    Disabling means PAUSE. A destructive reset is performed only by the explicit
    stop/start-again lifecycle action.
    """
    result = self.update_managed_account(int(account_id), enabled=bool(enabled))
    if enabled:
        _ORIGINAL_SET_STATUS(
            self,
            int(account_id),
            "connecting",
            "Auto trading resumed; preserved session state will continue",
        )
    else:
        _ORIGINAL_SET_STATUS(
            self,
            int(account_id),
            "manual_pause",
            "Auto trading paused; recovery and session state preserved",
        )
    return result


def _resume_preserving_or_resetting(
    self: Test2Repository,
    account_id: int,
    *,
    reset_recovery: bool,
) -> None:
    """Resume keeps state; Start Again clears all account-session recovery state."""
    with self.database.session() as session:
        state = session.get(AccountRiskState, int(account_id), with_for_update=True)
        if state is None:
            return
        if reset_recovery:
            state.session_profit = 0.0
            state.consecutive_losses = 0
            state.recovery_loss_debt = 0.0
            state.recovery_pending = False
            state.recovery_attempt_active = False
            state.recovery_pending_since = None
            state.protection_mode = "NORMAL_MODE"
            state.entered_virtual_mode_at = None
            state.virtual_observation_count = 0
            state.virtual_win_count = 0
            state.virtual_loss_count = 0
            state.current_virtual_loss_streak = 0
        # reset_recovery=False deliberately changes nothing except the timestamp.
        # This is the core Pause -> Resume guarantee.
        state.updated_at = utc_now()


def _status_with_automatic_pause(
    self: Test2Repository,
    account_id: int,
    execution_status: str,
    reason: str = "",
) -> None:
    status = str(execution_status or "inactive").strip().lower()
    with self.database.session() as session:
        row = session.get(ManagedAccount, int(account_id), with_for_update=True)
        if row is None:
            return
        if status in PAUSED_STATUSES:
            # Pause only. Never clear recovery debt, virtual-protection state or
            # session P/L because the user may fix the cause and press Resume.
            row.enabled = False
            row.updated_at = utc_now()
        row.execution_status = status[:30]
        row.execution_status_reason = str(reason or "")[:160]
        row.execution_status_updated_at = utc_now()


def pause_account(
    repository: Test2Repository,
    account_id: int,
    *,
    status: str = "manual_pause",
    reason: str = "Auto trading paused by user",
) -> None:
    repository.update_managed_account(int(account_id), enabled=False)
    _ORIGINAL_SET_STATUS(repository, int(account_id), status, reason)


def stop_account(
    repository: Test2Repository,
    account_id: int,
    *,
    reason: str = "Auto trading stopped; next start uses the configured base stake",
) -> None:
    repository.update_managed_account(int(account_id), enabled=False)
    _resume_preserving_or_resetting(repository, int(account_id), reset_recovery=True)
    _ORIGINAL_SET_STATUS(repository, int(account_id), STOPPED_STATUS, reason)


def account_session_profit(repository: Test2Repository, account_id: int) -> float:
    with repository.database.session() as session:
        state = session.get(AccountRiskState, int(account_id))
        return float(state.session_profit or 0.0) if state is not None else 0.0


def install_repository_account_lifecycle() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    Test2Repository.set_managed_account_enabled = _set_enabled_preserving_state
    Test2Repository.resume_managed_account = _resume_preserving_or_resetting
    Test2Repository.set_managed_account_execution_status = _status_with_automatic_pause
    _INSTALLED = True


def install_worker_account_lifecycle() -> None:
    """Install account-scoped risk-limit semantics before the production bot starts."""
    install_repository_account_lifecycle()

    from enhanced_bot import TradingBot, mask_account_id

    if getattr(TradingBot, "_account_lifecycle_installed", False):
        return

    def _enforce_account_risk_limit(
        self: TradingBot,
        token: str,
        account_id: str,
        state: dict[str, Any],
    ) -> str:
        managed_account_id = state.get("managed_account_id")
        if managed_account_id in {None, ""}:
            return ""

        # TP/SL are session-scoped. Pause/Resume preserves this number.
        # Stop/Start Again resets it to zero.
        session_profit = account_session_profit(
            self.repository,
            int(managed_account_id),
        )
        take_profit = max(0.0, float(state.get("take_profit", 0.0) or 0.0))
        stop_loss = max(0.0, float(state.get("stop_loss", 0.0) or 0.0))

        status = ""
        reason = ""
        if take_profit > 0 and session_profit >= take_profit - 0.005:
            status = "take_profit"
            reason = f"Take profit reached at {session_profit:.2f} USD; account paused"
        elif stop_loss > 0 and session_profit <= -stop_loss + 0.005:
            status = "stop_loss"
            reason = f"Stop loss reached at {session_profit:.2f} USD; account paused"
        if not status:
            return ""

        pause_account(
            self.repository,
            int(managed_account_id),
            status=status,
            reason=reason,
        )
        self.valid_clients = [item for item in self.valid_clients if item[0] != token]
        self.repository.audit(
            "ACCOUNT_RISK_LIMIT_PAUSED",
            "worker",
            socket.gethostname(),
            {
                "account_id_masked": mask_account_id(account_id),
                "limit": status,
                "session_profit": round(session_profit, 2),
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "recovery_state_preserved": True,
            },
        )
        self.logger.warning(
            "ACCOUNT_PAUSED account=%s reason=%s session_profit=%.2f "
            "recovery_state_preserved=true",
            mask_account_id(account_id),
            status,
            session_profit,
        )
        return status

    TradingBot._enforce_account_risk_limit = _enforce_account_risk_limit
    TradingBot._account_lifecycle_installed = True
