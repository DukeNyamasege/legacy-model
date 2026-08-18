from __future__ import annotations

"""Shared ownership rules for browser-direct versus server execution.

The browser is allowed to own low-latency live execution while it is online.  The
server worker may keep scanning state, but it must not create a proposal or BUY
while the browser lease is fresh.  If the browser disappears, the lease expires
and the existing server strategy may take over without requiring the browser.
"""

from datetime import datetime, timezone
from typing import Any

DIRECT_BROWSER_STATUS = "direct_browser"
DIRECT_BROWSER_LEASE_SECONDS = 20.0
DIRECT_BROWSER_HEARTBEAT_SECONDS = 5.0


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


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


def direct_browser_lease_age_seconds(row: Any, *, now: datetime | None = None) -> float:
    stamp = _aware(_row_value(row, "execution_status_updated_at"))
    if stamp is None:
        return float("inf")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current.astimezone(timezone.utc) - stamp).total_seconds())


def direct_browser_lease_fresh(row: Any, *, now: datetime | None = None) -> bool:
    status = str(_row_value(row, "execution_status", "") or "").strip().lower()
    if status != DIRECT_BROWSER_STATUS or not bool(_row_value(row, "enabled", False)):
        return False
    return direct_browser_lease_age_seconds(row, now=now) < DIRECT_BROWSER_LEASE_SECONDS


def direct_browser_lease_remaining_seconds(row: Any, *, now: datetime | None = None) -> float:
    if str(_row_value(row, "execution_status", "") or "").strip().lower() != DIRECT_BROWSER_STATUS:
        return 0.0
    return max(
        0.0,
        DIRECT_BROWSER_LEASE_SECONDS - direct_browser_lease_age_seconds(row, now=now),
    )
