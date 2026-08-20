from __future__ import annotations

"""Final ownership guard for browser-direct execution.

A transient worker/provider failure must not overwrite active browser execution.
TP, SL and the durable explicit-user hard-stop sentinel keep absolute priority.
Browser-direct v3 installs an additional worker offload authority after this module,
so live/manual browser accounts are never promoted into VPS provider execution.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app import tp_sl_manual_only_authority as lifecycle
from app.direct_execution_hard_stop_state import direct_hard_stop_active
from app.direct_execution_lease import (
    DIRECT_BROWSER_LEASE_SECONDS,
    DIRECT_BROWSER_STATUS,
    direct_browser_lease_fresh,
)
from app.models import ManagedAccount, RuntimePreference, utc_now
from app.repositories.test2_repository import Test2Repository


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_ORIGINAL_SET_STATUS: Any = None
_ORIGINAL_FORCE_RETRY: Any = None
_OWNER_PREFIX = "direct_execution:v1:"
_TARGET_STOPS = {"take_profit", "stop_loss"}


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _clean_automatic_reason(reason: str) -> str:
    """Remove stale terminal wording from a nonterminal automatic retry reason."""

    text = str(reason or "").strip()
    prefixes = (
        "trading stopped:",
        "auto trading stopped:",
        "auto-trading stopped:",
        "trading paused:",
    )
    changed = True
    while text and changed:
        changed = False
        lowered = text.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
                break
    return text or "Temporary execution fault"


def _owner_payload(session: Any, managed_id: int) -> dict[str, Any]:
    row = session.get(RuntimePreference, f"{_OWNER_PREFIX}{int(managed_id)}")
    if row is None:
        return {}
    try:
        payload = json.loads(str(row.preference_value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _fresh_owner_heartbeat(
    session: Any,
    row: ManagedAccount,
    managed_id: int,
) -> datetime | None:
    if direct_browser_lease_fresh(row):
        return _aware(row.execution_status_updated_at)

    owner = _owner_payload(session, managed_id)
    if str(owner.get("owner") or "browser").strip().lower() not in {
        "browser",
        "browser_direct_only",
    }:
        return None
    if not str(owner.get("epoch") or "").strip():
        return None

    stamp = _aware(owner.get("last_heartbeat_at") or owner.get("armed_at"))
    if stamp is None:
        return None
    age = max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    return stamp if age < DIRECT_BROWSER_LEASE_SECONDS else None


def _preserve_browser_owner(
    repository: Test2Repository,
    managed_id: int,
    reason: str,
) -> bool:
    with repository.database.session() as session:
        row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
        if row is None:
            return False
        if direct_hard_stop_active(session, int(managed_id)):
            return False
        if str(row.execution_status or "").strip().lower() in _TARGET_STOPS:
            return False

        heartbeat_at = _fresh_owner_heartbeat(session, row, int(managed_id))
        if heartbeat_at is None:
            return False

        row.enabled = True
        row.execution_status = DIRECT_BROWSER_STATUS
        row.execution_status_reason = (
            "Browser execution remains active; transient execution fault will retry. "
            f"{_clean_automatic_reason(reason)}"
        )[:160]
        # Never let a worker retry manufacture a new browser ownership timestamp.
        row.execution_status_updated_at = heartbeat_at
        row.updated_at = utc_now()
        return True


def _lease_aware_force_retry_state(
    repository: Test2Repository,
    managed_id: int,
    status: str,
    reason: str,
    *,
    require_enabled: bool,
) -> bool:
    if _preserve_browser_owner(repository, int(managed_id), reason or status):
        LOGGER.warning(
            "BROWSER_DIRECT_LEASE_PRESERVED managed_id=%s attempted_status=%s "
            "browser_owner=true automatic_retry=true",
            int(managed_id),
            str(status or ""),
        )
        return True

    original = _ORIGINAL_FORCE_RETRY
    if original is None:
        return False
    return bool(
        original(
            repository,
            int(managed_id),
            status,
            _clean_automatic_reason(reason),
            require_enabled=require_enabled,
        )
    )


def _lease_aware_set_status(
    self: Test2Repository,
    account_id: int,
    execution_status: str,
    reason: str = "",
) -> None:
    original = _ORIGINAL_SET_STATUS
    if original is None:
        return

    requested = str(execution_status or "inactive").strip().lower()
    if lifecycle._terminal_allowed(self, int(account_id), requested):
        original(self, int(account_id), requested, reason)
        return

    if _preserve_browser_owner(self, int(account_id), reason or requested):
        LOGGER.warning(
            "BROWSER_DIRECT_STATUS_MUTATION_BLOCKED managed_id=%s attempted_status=%s browser_owner=true",
            int(account_id),
            requested,
        )
        return

    original(
        self,
        int(account_id),
        requested,
        _clean_automatic_reason(reason),
    )


def install_browser_direct_lease_preservation_authority() -> None:
    """Install after the TP/SL/manual lifecycle authority."""

    global _INSTALLED, _ORIGINAL_SET_STATUS, _ORIGINAL_FORCE_RETRY
    if _INSTALLED:
        return

    _ORIGINAL_SET_STATUS = Test2Repository.set_managed_account_execution_status
    _ORIGINAL_FORCE_RETRY = lifecycle._force_retry_state

    lifecycle._force_retry_state = _lease_aware_force_retry_state
    Test2Repository.set_managed_account_execution_status = _lease_aware_set_status

    Test2Repository._browser_direct_lease_preservation_installed = True
    Test2Repository._browser_direct_worker_retry_extends_lease = False
    _INSTALLED = True
