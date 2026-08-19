from __future__ import annotations

"""Convert repository-level automatic quarantines into durable retry states.

Explicit account disable remains untouched. This wrapper only preserves accounts
that were enabled immediately before an automatic quarantine/token-rejection path.
"""

from typing import Any

from sqlalchemy import select

from app.direct_execution_hard_stop_state import direct_hard_stop_active
from app.models import ManagedAccount, utc_now
from app.repositories.test2_repository import Test2Repository


_INSTALLED = False
_ORIGINAL_QUARANTINE: Any = None
_ORIGINAL_DISCARD_TOKEN: Any = None


def _retry_status(status: str, reason: str) -> str:
    text = f"{status} {reason}".lower()
    return "reconnecting" if any(word in text for word in ("token", "credential", "connect", "socket", "session")) else "waiting_for_condition"


def _explicit_manual_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    return any(
        marker in text
        for marker in (
            "user stop",
            "user pressed",
            "manual stop",
            "manually stopped",
            "manual pause",
            "paused manually",
            "stopped manually",
            "start is required before execution",
            "auto trading stopped for this account mode",
        )
    )


def _preserve_enabled_retry(repository: Test2Repository, managed_id: int, status: str, reason: str) -> None:
    with repository.database.session() as session:
        row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
        if row is None:
            return
        if direct_hard_stop_active(session, int(managed_id)):
            return
        current = str(row.execution_status or "").strip().lower()
        current_reason = str(row.execution_status_reason or "")
        if current in {"take_profit", "stop_loss"}:
            return
        if current in {"manual_pause", "stopped"} and _explicit_manual_reason(current_reason):
            return
        # A generic/synthetic "stopped" row without a hard-stop sentinel or an
        # explicit manual reason is not a user exit. Keep the already-started
        # account enabled and let its repair path continue.
        row.enabled = True
        row.execution_status = _retry_status(status, reason)
        row.execution_status_reason = (
            f"Auto Trading remains active; automatic recovery required. {reason or status}"
        )[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()


def install_never_auto_stop_repository_authority() -> None:
    global _INSTALLED, _ORIGINAL_QUARANTINE, _ORIGINAL_DISCARD_TOKEN
    if _INSTALLED:
        return

    _ORIGINAL_QUARANTINE = Test2Repository.quarantine_managed_account
    _ORIGINAL_DISCARD_TOKEN = Test2Repository.discard_rejected_trading_token

    def quarantine_as_retry(
        self: Test2Repository,
        account_id: int,
        execution_status: str,
        reason: str,
    ) -> None:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            was_enabled = bool(row.enabled) if row is not None else False
        if not was_enabled:
            original = _ORIGINAL_QUARANTINE
            if original is not None:
                original(self, account_id, execution_status, reason)
            return
        _preserve_enabled_retry(self, int(account_id), execution_status, reason)

    def discard_token_without_terminal_stop(
        self: Test2Repository,
        account_id: int,
        *,
        reason: str,
    ) -> list[int]:
        original = _ORIGINAL_DISCARD_TOKEN
        if original is None:
            return []
        with self.database.session() as session:
            enabled_before = {
                int(row.id): bool(row.enabled)
                for row in session.scalars(select(ManagedAccount)).all()
            }
        affected = list(original(self, int(account_id), reason=reason) or [])
        for managed_id in affected:
            if enabled_before.get(int(managed_id), False):
                _preserve_enabled_retry(self, int(managed_id), "token_required", reason)
        return affected

    Test2Repository.quarantine_managed_account = quarantine_as_retry
    Test2Repository.discard_rejected_trading_token = discard_token_without_terminal_stop
    Test2Repository._never_auto_stop_repository_authority_installed = True
    _INSTALLED = True
