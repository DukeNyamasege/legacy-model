from __future__ import annotations

"""Final worker authority for a fresh browser-direct execution lease.

Browser-direct live trading deliberately removes the VPS worker from the per-tick
and per-BUY hot path.  A fresh ``direct_browser`` row therefore means "the browser
owns financial execution", not "this account should be normalized to stopped".

Several older lifecycle/repair layers write execution statuses while they perform
account discovery.  This module installs last in the worker and prevents those
housekeeping writes from replacing a fresh browser lease.  It deliberately does
*not* refresh the lease timestamp; only the browser heartbeat may extend ownership.
Explicit user Stop writes the ManagedAccount row directly through the control-plane
route, so it remains authoritative and is not intercepted here.
"""

from typing import Any

from app.direct_execution_lease import DIRECT_BROWSER_STATUS, direct_browser_lease_fresh
from app.models import ManagedAccount
from app.repositories.test2_repository import Test2Repository

_INSTALLED = False


def install_direct_browser_runtime_authority() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_set_status = Test2Repository.set_managed_account_execution_status

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

    Test2Repository.set_managed_account_execution_status = browser_owned_status_guard
    Test2Repository._direct_browser_runtime_authority_installed = True
    _INSTALLED = True
