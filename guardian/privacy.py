from __future__ import annotations

from typing import Any


_SAFE_SENSITIVE_AGGREGATES = {
    "active_accounts",
    "registered_traders",
    "total_registered_traders",
    "trading_now",
    "active_traders",
    "trading_ready_accounts",
    "account_type",
    "dashboard_account_type",
}

_SENSITIVE_KEY_PARTS = (
    "account",
    "balance",
    "token",
    "credential",
    "secret",
    "oauth",
    "login",
    "email",
    "phone",
    "session",
    "client",
    "username",
    "user_id",
    "managed_id",
    "managed_account",
    "cookie",
    "authorization",
)


def _sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized in _SAFE_SENSITIVE_AGGREGATES:
        return False
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def sanitize_strategy_metrics(value: Any, *, depth: int = 0) -> Any:
    """Return aggregate-only metrics suitable for an external model request."""

    if depth > 8:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:250]:
            key = str(raw_key)[:160]
            if _sensitive_key(key):
                continue
            if isinstance(raw_value, list) and raw_value and any(
                isinstance(item, dict) for item in raw_value
            ):
                # Per-trade/per-account rows are never needed for the scheduled
                # high-level strategy advisory request.
                continue
            result[key] = sanitize_strategy_metrics(raw_value, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        primitives = [
            item
            for item in value[:100]
            if item is None or isinstance(item, (bool, int, float, str))
        ]
        return [sanitize_strategy_metrics(item, depth=depth + 1) for item in primitives]
    return str(type(value).__name__)
