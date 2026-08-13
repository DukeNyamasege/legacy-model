from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from app.models import ManagedAccount, RuntimePreference, utc_now


PREFERENCE_PREFIX = "session_risk_limits:v1:"


@dataclass(frozen=True)
class SessionRiskLimits:
    """Immutable TP/SL limits for one fresh Auto Trading session.

    Take profit is always represented as a positive threshold. Stop loss is always
    represented as a negative threshold. A zero value disables that limit.
    """

    take_profit: float
    stop_loss: float
    started_at: str


def normalize_take_profit(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, round(number, 2))


def normalize_stop_loss(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    magnitude = max(0.0, round(abs(number), 2))
    return -magnitude if magnitude > 0 else 0.0


def preference_key(managed_account_id: int) -> str:
    return f"{PREFERENCE_PREFIX}{int(managed_account_id)}"


def _limits_from_account(account: ManagedAccount | None) -> SessionRiskLimits:
    return SessionRiskLimits(
        take_profit=normalize_take_profit(getattr(account, "take_profit", 0.0)),
        stop_loss=normalize_stop_loss(getattr(account, "stop_loss", 0.0)),
        started_at="",
    )


def snapshot_session_risk_limits(
    session: Any,
    account: ManagedAccount,
) -> SessionRiskLimits:
    """Freeze the current account settings at the moment a fresh session starts."""

    started_at = utc_now().isoformat()
    limits = SessionRiskLimits(
        take_profit=normalize_take_profit(account.take_profit),
        stop_loss=normalize_stop_loss(account.stop_loss),
        started_at=started_at,
    )
    key = preference_key(int(account.id))
    value = json.dumps(
        {
            "take_profit": limits.take_profit,
            "stop_loss": limits.stop_loss,
            "started_at": limits.started_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=value))
    else:
        row.preference_value = value
        row.updated_at = utc_now()
    return limits


def read_session_risk_limits(
    session: Any,
    managed_account_id: int,
    *,
    account: ManagedAccount | None = None,
) -> SessionRiskLimits:
    """Read the frozen Start-session limits, falling back only for old sessions."""

    row = session.get(RuntimePreference, preference_key(int(managed_account_id)))
    raw = str(row.preference_value or "") if row is not None else ""
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return SessionRiskLimits(
                    take_profit=normalize_take_profit(payload.get("take_profit")),
                    stop_loss=normalize_stop_loss(payload.get("stop_loss")),
                    started_at=str(payload.get("started_at") or ""),
                )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    resolved_account = account or session.get(ManagedAccount, int(managed_account_id))
    return _limits_from_account(resolved_account)
