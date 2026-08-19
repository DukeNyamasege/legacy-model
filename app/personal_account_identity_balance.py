from __future__ import annotations

from app.route_utils import remove_route as _remove_route

import time
from typing import Any, Callable

from fastapi import Request

import app.api as base_api

_INSTALLED = False




def _current_me_endpoint(app: Any) -> Callable[[Request], dict[str, Any]] | None:
    for route in reversed(list(app.router.routes)):
        if getattr(route, "path", None) == "/me" and "GET" in set(getattr(route, "methods", set()) or set()):
            endpoint = getattr(route, "endpoint", None)
            if callable(endpoint):
                return endpoint
    return None


def _identity_payload(account: dict[str, Any] | None) -> dict[str, Any]:
    if not account:
        return {}
    account_id = str(account.get("account_id") or "").strip()
    account_type = str(account.get("account_type") or "demo").strip().lower()
    return {
        "account_id_full": account_id,
        "login_id": account_id,
        "display_account_id": account_id,
        "account_type_label": "Real" if account_type == "real" else "Demo",
        "account_prefix": account_id[:3].upper() if len(account_id) >= 3 else account_id.upper(),
    }


def _force_personal_balance_refresh(account: dict[str, Any]) -> dict[str, Any] | None:
    account_id = str(account.get("account_id") or "").strip()
    if not account_id:
        return None

    # The old personal dashboard refresh TTL was 30 seconds. That is too slow
    # for a trading UI because the user expects balance to move immediately
    # after buy/settlement. Keep a short throttle so refresh is near-real-time
    # without hammering Deriv on every background heartbeat.
    ttl_seconds = 2.0
    now = time.monotonic()
    lock = getattr(base_api, "PERSONAL_ACCOUNT_REFRESH_LOCK", None)
    last_by_account = getattr(base_api, "PERSONAL_ACCOUNT_REFRESH", None)
    inflight = getattr(base_api, "PERSONAL_ACCOUNT_REFRESH_INFLIGHT", None)
    refresh = getattr(base_api, "_refresh_personal_account_snapshot", None)
    if lock is None or last_by_account is None or inflight is None or not callable(refresh):
        return None

    with lock:
        last = float(last_by_account.get(account_id, 0.0) or 0.0)
        if now - last < ttl_seconds:
            return None
        if account_id in inflight:
            return None
        inflight.add(account_id)
    try:
        return refresh(account)
    except Exception:
        # /me must never fail because the provider balance refresh failed.
        return None


def install_personal_account_identity_balance(app: Any) -> None:
    """Expose exact logged-in account ID and refresh personal balance quickly."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_me = _current_me_endpoint(app)
    _remove_route(app, "/me", "GET")

    @app.get("/me")
    def me_with_identity_and_live_balance(request: Request) -> dict[str, Any]:
        payload: dict[str, Any]
        if original_me is not None:
            payload = dict(original_me(request) or {})
        else:
            payload = dict(base_api.get_me(request) or {})
        if not payload.get("authenticated"):
            return payload

        account = base_api.get_current_account(request)
        payload.update(_identity_payload(account))
        if account:
            refreshed = _force_personal_balance_refresh(account)
            if isinstance(refreshed, dict) and refreshed:
                if "balance" in refreshed:
                    payload["balance"] = refreshed.get("balance")
                if "currency" in refreshed:
                    payload["currency"] = refreshed.get("currency")
                if "status" in refreshed:
                    payload["status"] = refreshed.get("status")
        return payload

    app.state.personal_account_identity_balance_installed = True
    _INSTALLED = True
