from __future__ import annotations

from typing import Any, Callable

from fastapi import Request

import app.api as base_api


_INSTALLED = False


def _route_endpoint(app: Any, path: str, method: str) -> Callable[..., Any] | None:
    expected = method.upper()
    for route in reversed(list(app.router.routes)):
        if getattr(route, "path", None) != path:
            continue
        if expected not in set(getattr(route, "methods", set()) or set()):
            continue
        endpoint = getattr(route, "endpoint", None)
        if callable(endpoint):
            return endpoint
    return None


def _remove_route(app: Any, path: str, method: str) -> None:
    expected = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        )
    ]


def _force_next_balance_refresh(request: Request) -> None:
    """Expire the per-account balance throttle for the newly selected account."""

    try:
        account = base_api.get_current_account(request)
        account_id = str((account or {}).get("account_id") or "").strip()
        if not account_id:
            return
        lock = getattr(base_api, "PERSONAL_ACCOUNT_REFRESH_LOCK", None)
        last_by_account = getattr(base_api, "PERSONAL_ACCOUNT_REFRESH", None)
        if lock is None or last_by_account is None:
            return
        with lock:
            last_by_account[account_id] = 0.0
    except Exception:
        return


def install_seamless_account_switch(app: Any) -> None:
    """Make Demo/Real switching return the new account identity and balance atomically.

    The legacy endpoint changed the session's managed-account pointer and returned
    only ``success`` and ``account_type``. The browser therefore painted the new
    mode label beside the previous account's balance until a later dashboard
    refresh completed. Keep the existing switch authority intact, then immediately
    execute the final /me authority for the newly selected account and return that
    snapshot in the same response.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_switch = _route_endpoint(app, "/me/switch-account", "POST")
    me_endpoint = _route_endpoint(app, "/me", "GET")
    if original_switch is None or me_endpoint is None:
        return

    _remove_route(app, "/me/switch-account", "POST")

    @app.post("/me/switch-account")
    def seamless_switch(
        request: Request,
        body: base_api.PersonalAccountSwitchRequest,
    ) -> dict[str, Any]:
        result = dict(original_switch(request, body) or {})
        _force_next_balance_refresh(request)
        me = dict(me_endpoint(request) or {})
        if me.get("authenticated"):
            result.update(
                {
                    "account_type": me.get("account_type", result.get("account_type")),
                    "account_id": me.get("account_id"),
                    "account_id_masked": me.get("account_id_masked"),
                    "balance": me.get("balance"),
                    "currency": me.get("currency"),
                    "me": me,
                }
            )
        return result

    app.state.seamless_account_switch_installed = True
    _INSTALLED = True
