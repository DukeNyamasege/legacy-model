from __future__ import annotations

import json
from typing import Any

from sqlalchemy.exc import IntegrityError

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
    """Persist the independent Stop sentinel without a concurrent-insert race.

    Two browser/UI Stop requests can legitimately arrive together. The old
    get-then-insert sequence let both transactions observe a missing preference
    and one then failed the primary-key insert with HTTP 500. A SAVEPOINT keeps
    the caller transaction intact: the losing insert rolls back only its nested
    write, then updates the row created by the winning request.
    """

    key = direct_hard_stop_key(managed_account_id)
    now = utc_now()
    payload = json.dumps(
        {
            "active": True,
            "managed_account_id": int(managed_account_id),
            "stopped_at": now.isoformat(),
            "reason": str(reason or "User pressed Stop")[:160],
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    row = session.get(RuntimePreference, key)
    if row is not None:
        row.preference_value = payload
        row.updated_at = now
        return

    try:
        with session.begin_nested():
            session.add(
                RuntimePreference(
                    preference_key=key,
                    preference_value=payload,
                    updated_at=now,
                )
            )
            session.flush()
        return
    except IntegrityError:
        # A concurrent Stop created the same sentinel first. The SAVEPOINT has
        # already rolled back only our duplicate insert; keep Stop idempotent.
        pass

    row = session.get(RuntimePreference, key, populate_existing=True)
    if row is None:
        # Defensive fallback for unusual isolation/driver behaviour. Raising here
        # is safer than reporting Stop success without a durable financial fence.
        raise RuntimeError("Direct hard-stop sentinel could not be persisted")
    row.preference_value = payload
    row.updated_at = now


def clear_direct_hard_stop(session: Any, managed_account_id: int) -> None:
    row = session.get(RuntimePreference, direct_hard_stop_key(managed_account_id))
    if row is not None:
        session.delete(row)
