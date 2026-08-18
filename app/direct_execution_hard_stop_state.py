from __future__ import annotations

import json
from typing import Any

from app.models import RuntimePreference, utc_now


HARD_STOP_PREFIX = "direct_execution:hard_stop:v2:"


def direct_hard_stop_key(managed_account_id: int) -> str:
    return f"{HARD_STOP_PREFIX}{int(managed_account_id)}"


def direct_hard_stop_active(session: Any, managed_account_id: int) -> bool:
    row = session.get(RuntimePreference, direct_hard_stop_key(managed_account_id))
    if row is None:
        return False
    try:
        payload = json.loads(str(row.preference_value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return payload.get("active", True) is not False


def set_direct_hard_stop(
    session: Any,
    managed_account_id: int,
    *,
    reason: str = "User pressed Stop",
) -> None:
    key = direct_hard_stop_key(managed_account_id)
    payload = json.dumps(
        {
            "active": True,
            "managed_account_id": int(managed_account_id),
            "stopped_at": utc_now().isoformat(),
            "reason": str(reason or "User pressed Stop")[:160],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=payload))
    else:
        row.preference_value = payload
        row.updated_at = utc_now()


def clear_direct_hard_stop(session: Any, managed_account_id: int) -> None:
    row = session.get(RuntimePreference, direct_hard_stop_key(managed_account_id))
    if row is not None:
        session.delete(row)
