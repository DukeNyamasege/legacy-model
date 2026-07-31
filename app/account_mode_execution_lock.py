from __future__ import annotations

from typing import Any

from app.models import ManagedAccount, utc_now
from app.repositories.test2_repository import Test2Repository

_INSTALLED = False

STOPPED_LIKE_STATUSES = {
    "inactive",
    "disabled",
    "stopped",
}

PAUSED_LIKE_STATUSES = {
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
    "real_disabled",
}

AUTO_PROMOTION_STATUSES = {
    "validating",
    "connecting",
    "active",
    "reconnecting",
    "base_stake_protection",
    "recovery_pending",
}


def account_lifecycle_from_row(row: Any) -> str:
    status = str(getattr(row, "execution_status", "inactive") or "inactive").strip().lower()
    enabled = bool(getattr(row, "enabled", False))
    if status in STOPPED_LIKE_STATUSES:
        return "stopped"
    if not enabled or status in PAUSED_LIKE_STATUSES:
        return "paused"
    return "running"


def _manual_locking_set_status(original_set_status):
    def set_status(
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
            current_status = str(row.execution_status or "inactive").strip().lower()
            current_lifecycle = account_lifecycle_from_row(row)

            # Worker validation, OAuth refresh, balance refresh, and dashboard
            # repair jobs must never promote a mode/account that the user has not
            # manually started.  The only allowed promotion path is the explicit
            # Start/Resume endpoint, which calls set_managed_account_enabled(True)
            # and bypasses this guard through the original status setter.
            if (
                current_lifecycle in {"stopped", "paused"}
                and not bool(row.enabled)
                and status in AUTO_PROMOTION_STATUSES
            ):
                row.execution_status = current_status[:30]
                if not row.execution_status_reason:
                    if current_lifecycle == "stopped":
                        row.execution_status_reason = (
                            "Auto trading has not been started for this account mode"
                        )[:160]
                    else:
                        row.execution_status_reason = (
                            "Auto trading is paused for this account mode"
                        )[:160]
                row.execution_status_updated_at = utc_now()
                row.updated_at = utc_now()
                return

        original_set_status(self, int(account_id), status, reason)
    return set_status


def _manual_start_set_enabled(original_update_account, original_set_status):
    def set_enabled(
        self: Test2Repository,
        account_id: int,
        enabled: bool,
    ) -> dict[str, Any]:
        result = original_update_account(int(account_id), enabled=bool(enabled))
        if enabled:
            original_set_status(
                self,
                int(account_id),
                "connecting",
                "Auto trading started manually for this account mode",
            )
        else:
            original_set_status(
                self,
                int(account_id),
                "manual_pause",
                "Auto trading paused manually for this account mode",
            )
        return result
    return set_enabled


def stop_mode_account(repository: Test2Repository, account_id: int) -> None:
    """Hard stop one account mode without touching the sibling Demo/Real row."""
    repository.update_managed_account(int(account_id), enabled=False)
    with repository.database.session() as session:
        row = session.get(ManagedAccount, int(account_id), with_for_update=True)
        if row is None:
            return
        row.enabled = False
        row.execution_status = "stopped"
        row.execution_status_reason = (
            "Auto trading stopped for this account mode; Start is required before execution"
        )[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()


def install_account_mode_execution_lock() -> None:
    """Require explicit Start per Demo/Real account mode.

    Demo and Real can both exist and both can be supported, but neither mode is
    allowed to auto-start because the sibling mode is running or because OAuth/API
    token refresh repaired its credential.  Only the current mode's Start/Resume
    endpoint can enable that exact ManagedAccount row.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_update_account = Test2Repository.update_managed_account
    current_set_status = Test2Repository.set_managed_account_execution_status

    Test2Repository.set_managed_account_execution_status = _manual_locking_set_status(
        current_set_status
    )
    Test2Repository.set_managed_account_enabled = _manual_start_set_enabled(
        original_update_account,
        current_set_status,
    )
    Test2Repository._account_mode_execution_lock_installed = True
    _INSTALLED = True
