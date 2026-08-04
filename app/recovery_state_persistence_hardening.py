from __future__ import annotations

from typing import Any

from app.models import AccountRiskState, utc_now
from app.repositories.rf_dir5_repository import (
    REAL_RECOVERY_PENDING,
    RFDir5Repository,
    VIRTUAL_WAITING_FOR_WIN,
)

_INSTALLED = False


def _persist_recovery_attempt(
    repository: RFDir5Repository,
    managed_account_id: int,
) -> bool:
    """Persist or verify one active recovery attempt atomically.

    The strict AIDR planner already persists ``recovery_attempt_active=True``
    before a private WebSocket buy. The legacy follow-up call treated that valid
    already-persisted state as failure and logged ``state_persisted=False``.
    Recovery start is now idempotent: an existing active attempt is success, while
    virtual-protection and debt-free accounts remain blocked.
    """

    with repository.database.session() as session:
        state = session.get(
            AccountRiskState,
            int(managed_account_id),
            with_for_update=True,
        )
        if state is None:
            return False
        if state.protection_mode == VIRTUAL_WAITING_FOR_WIN:
            return False

        debt = float(state.recovery_loss_debt or 0.0)
        if debt <= 0.009:
            return False

        if bool(state.recovery_attempt_active):
            # Original semantics clear pending once the real recovery contract is
            # considered active. Returning True makes restart-safe verification
            # distinguish a persisted state from a genuine failure.
            state.recovery_pending = False
            state.protection_mode = REAL_RECOVERY_PENDING
            state.recovery_pending_since = state.recovery_pending_since or utc_now()
            state.updated_at = utc_now()
            return True

        if not bool(state.recovery_pending):
            return False

        state.recovery_pending = False
        state.recovery_attempt_active = True
        state.protection_mode = REAL_RECOVERY_PENDING
        state.recovery_pending_since = state.recovery_pending_since or utc_now()
        state.updated_at = utc_now()
        return True


def install_recovery_state_persistence_hardening() -> None:
    """Replace the non-idempotent recovery-start marker before bot startup."""

    global _INSTALLED
    if _INSTALLED:
        return

    def mark_recovery_attempt_started(
        self: RFDir5Repository,
        managed_account_id: int,
    ) -> bool:
        return _persist_recovery_attempt(self, int(managed_account_id))

    RFDir5Repository.mark_recovery_attempt_started = mark_recovery_attempt_started
    RFDir5Repository._recovery_state_persistence_hardening_installed = True
    _INSTALLED = True
