from __future__ import annotations

from typing import Any

from app.models import ManagedAccount, utc_now
from app.repositories.test2_repository import Test2Repository

_INSTALLED = False

STOPPED_LIKE_STATUSES = {
    "inactive",
    "disabled",
    "stopped",
    # Real trading is no longer a permanent account pause.  If a stale VPS gate
    # marked a real account this way, the trader must be able to press Start Auto
    # Trade again after the fixed real gate is deployed.
    "real_disabled",
}

SETTLEMENT_ONLY_STATUS = "settlement_only"

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
}

STARTING_LIKE_STATUSES = {
    "starting",
    "validating",
    "connecting",
    "reconnecting",
}

AUTO_PROMOTION_STATUSES = {
    "validating",
    "connecting",
    "active",
    "reconnecting",
    "base_stake_protection",
    "recovery_pending",
    "virtual_protection",
}


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def account_lifecycle_from_row(row: Any) -> str:
    status = str(_row_value(row, "execution_status", "inactive") or "inactive").strip().lower()
    enabled = bool(_row_value(row, "enabled", False))
    if status == SETTLEMENT_ONLY_STATUS:
        return "settlement"
    if status in STOPPED_LIKE_STATUSES:
        return "stopped"
    if not enabled or status in PAUSED_LIKE_STATUSES:
        return "paused"
    if status in STARTING_LIKE_STATUSES:
        return "starting"
    return "running"


def account_allows_new_execution(row: Any) -> bool:
    """True only after the trader explicitly pressed Start/Resume.

    Logged-in or linked accounts are not execution accounts by default. The
    per-account lifecycle is:

        STOPPED -> STARTING -> RUNNING -> STOPPING -> STOPPED

    ``settlement_only`` is intentionally excluded: it may keep a private stream
    alive just long enough to finish already-open contracts, but it must never
    enter proposal or purchase scopes.
    """

    return account_lifecycle_from_row(row) in {"starting", "running"}


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
            # repair jobs must never promote a mode/account that the user stopped
            # or paused. The previous guard required enabled=false, which allowed
            # an impossible mixed row (status=stopped, enabled=true) to be promoted
            # back to active. Stopped/paused status alone is now enough to block
            # automatic promotion; only the explicit Start/Resume route can move
            # the account forward.
            if current_lifecycle in {"stopped", "paused", "settlement"} and status in AUTO_PROMOTION_STATUSES:
                if current_lifecycle in {"stopped", "settlement"}:
                    row.enabled = False
                row.execution_status = current_status[:30]
                if not row.execution_status_reason:
                    if current_lifecycle == "settlement":
                        row.execution_status_reason = (
                            "Existing contracts are settling; Start is required for new execution"
                        )[:160]
                    elif current_lifecycle == "stopped":
                        row.execution_status_reason = (
                            "Auto trading has been stopped for this account mode"
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
                "Auto trading starting manually for this account mode",
            )
        else:
            original_set_status(
                self,
                int(account_id),
                "stopped",
                "Auto trading stopped manually for this account mode",
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
