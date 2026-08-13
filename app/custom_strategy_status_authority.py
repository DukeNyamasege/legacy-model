from __future__ import annotations

from typing import Any

from app.repositories.test2_repository import Test2Repository


_INSTALLED = False
_ORIGINAL_SET_STATUS: Any = None


def install_custom_strategy_status_authority() -> None:
    """Keep transport connectivity separate from Custom Strategy lifecycle state.

    The private WebSocket reports connectivity independently from strategy scanning.
    A transport-level `active` update must not replace `waiting_for_condition`,
    `executing`, recovery, or virtual-protection state in the account row.
    """

    global _INSTALLED, _ORIGINAL_SET_STATUS
    if _INSTALLED:
        return

    _ORIGINAL_SET_STATUS = Test2Repository.set_managed_account_execution_status

    def set_status(
        self: Test2Repository,
        account_id: int,
        execution_status: str,
        reason: str = "",
    ) -> None:
        status = str(execution_status or "").strip().lower()
        message = str(reason or "").strip().lower()

        # ClientSession uses this message only to announce transport connectivity.
        # The Custom Strategy runtime immediately publishes the actual account
        # lifecycle (starting/waiting/executing/running/virtual/recovery). Do not
        # let a reconnect or readiness refresh overwrite that state with `active`.
        if status == "active" and message == "private trading connection is active":
            return

        _ORIGINAL_SET_STATUS(self, int(account_id), execution_status, reason)

    Test2Repository.set_managed_account_execution_status = set_status
    Test2Repository._custom_strategy_status_authority_installed = True
    _INSTALLED = True
