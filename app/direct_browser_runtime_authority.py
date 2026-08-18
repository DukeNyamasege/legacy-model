from __future__ import annotations

"""Final worker authority for a fresh browser-direct execution lease.

Browser-direct live trading deliberately removes the VPS worker from the per-tick
and per-BUY hot path. A fresh ``direct_browser`` row therefore means "the browser
owns financial execution", not "this account should be normalized to stopped".

Several older lifecycle/repair layers write execution statuses while they perform
account discovery. This module installs last in the worker and prevents those
housekeeping writes from replacing a fresh browser lease. It also teaches the old
liveness watchdog that an intentionally browser-owned account is healthy even
though it has no VPS private WebSocket/runtime object.

The guard deliberately does *not* refresh the lease timestamp; only the browser
heartbeat may extend ownership. Explicit user Stop writes the ManagedAccount row
directly through the control-plane route, so it remains authoritative.
"""

from dataclasses import dataclass
from typing import Any

import app.execution_stop_reason_authority as stop_reason_authority
from app.direct_execution_lease import DIRECT_BROWSER_STATUS, direct_browser_lease_fresh
from app.models import ManagedAccount
from app.repositories.test2_repository import Test2Repository

_INSTALLED = False


@dataclass(frozen=True)
class _BrowserOwnedSession:
    """Watchdog-only sentinel; never used for provider traffic."""

    is_connected: bool = True


_BROWSER_SESSION = _BrowserOwnedSession()
_BROWSER_RUNTIME = {"owner": "browser_direct", "financial_transport": "browser"}


def _browser_lease_fresh(repository: Test2Repository, managed_id: int) -> bool:
    with repository.database.session() as session:
        row = session.get(ManagedAccount, int(managed_id))
        return bool(row is not None and direct_browser_lease_fresh(row))


def install_direct_browser_runtime_authority() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_set_status = Test2Repository.set_managed_account_execution_status
    original_private_session = stop_reason_authority._private_session_for_account
    original_direct_runtime = stop_reason_authority._direct_runtime_for_account

    def browser_owned_status_guard(
        self: Test2Repository,
        account_id: int,
        execution_status: str,
        reason: str = "",
    ) -> Any:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if (
                row is not None
                and str(row.execution_status or "").strip().lower() == DIRECT_BROWSER_STATUS
                and bool(row.enabled)
                and direct_browser_lease_fresh(row)
            ):
                # Do not write *anything* here. In particular, do not touch
                # execution_status_updated_at because that timestamp is the lease
                # clock and only a browser heartbeat may extend it.
                return None
        return original_set_status(self, int(account_id), execution_status, reason)

    def browser_aware_private_session(bot: Any, managed_id: int) -> Any | None:
        if _browser_lease_fresh(bot.repository, int(managed_id)):
            return _BROWSER_SESSION
        return original_private_session(bot, int(managed_id))

    def browser_aware_runtime(bot: Any, managed_id: int) -> Any | None:
        if _browser_lease_fresh(bot.repository, int(managed_id)):
            return _BROWSER_RUNTIME
        return original_direct_runtime(bot, int(managed_id))

    Test2Repository.set_managed_account_execution_status = browser_owned_status_guard
    stop_reason_authority._private_session_for_account = browser_aware_private_session
    stop_reason_authority._direct_runtime_for_account = browser_aware_runtime
    Test2Repository._direct_browser_runtime_authority_installed = True
    _INSTALLED = True
