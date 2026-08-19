from __future__ import annotations

from app.route_utils import remove_route as _remove_route

"""Immediate financial Stop authority for the Full-VPS hybrid runtime.

The user-facing Stop action must not wait behind the worker's ManagedAccount row
lock before new financial purchases become forbidden.  This module persists a
small, independent hard-stop sentinel first.  The worker's final pre-BUY fence
checks that sentinel on every purchase scope.  ManagedAccount lifecycle cleanup is
then allowed to finish in the background.

An explicit successful Start/Resume/Direct-Arm clears the sentinel.  Reset/Clear
never changes it.
"""

from typing import Any

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

import app.api as base_api
from app.direct_execution_hard_stop_state import (
    clear_direct_hard_stop,
    direct_hard_stop_active,
    set_direct_hard_stop,
)
from app.direct_execution_lease import (
    DIRECT_BROWSER_STATUS,
    direct_browser_lease_remaining_seconds,
)
from app.models import ManagedAccount, RuntimePreference, utc_now


_INSTALLED = False
_OWNER_PREFIX = "direct_execution:v1:"
_START_PATHS = {
    "/me/direct-execution/arm",
    "/api/me/direct-execution/arm",
    "/me/resume-trading",
    "/api/me/resume-trading",
}
_STOP_PATHS = {
    "/me/stop-trading",
    "/api/me/stop-trading",
    "/me/pause-trading",
    "/api/me/pause-trading",
}




def _current_managed_id(request: Request) -> int:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(account["id"])


def _write_stop_sentinel(managed_id: int, reason: str) -> None:
    with base_api.DATABASE.session() as session:
        set_direct_hard_stop(
            session,
            int(managed_id),
            reason=str(reason or "User pressed Stop"),
        )


def _clear_stop_sentinel(managed_id: int) -> None:
    with base_api.DATABASE.session() as session:
        clear_direct_hard_stop(session, int(managed_id))


def _normalize_stopped_account(managed_id: int) -> None:
    """Best-effort lifecycle cleanup after the financial stop is already durable."""

    try:
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
            if row is None:
                return
            row.enabled = False
            row.execution_status = "stopped"
            row.execution_status_reason = (
                "User Stop is active. New browser and server purchases are forbidden."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            owner = session.get(RuntimePreference, f"{_OWNER_PREFIX}{int(managed_id)}")
            if owner is not None:
                session.delete(owner)
    except Exception:
        # The independent sentinel remains authoritative even if this cosmetic /
        # lifecycle normalization has to be repaired by a later control request.
        pass


def install_vps_direct_hard_stop_v2(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Legacy stop/pause routes may still be called by an older cached shell.  Fence
    # money before entering those potentially slower handlers.  A successful
    # explicit Start clears the fence only after its own server mutation succeeds.
    @app.middleware("http")
    async def direct_hard_stop_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = str(request.url.path or "")
        method = str(request.method or "GET").upper()
        managed_id: int | None = None

        if method == "POST" and path in (_STOP_PATHS | _START_PATHS):
            try:
                account = base_api.get_current_account(request)
                if account:
                    managed_id = int(account["id"])
            except Exception:
                managed_id = None

        if managed_id is not None and path in _STOP_PATHS:
            try:
                _write_stop_sentinel(managed_id, "Legacy Stop/Pause request")
            except Exception:
                # The route itself may still complete the stop.  Do not turn a
                # recoverable sentinel write failure into a new user-facing error.
                pass

        response = await call_next(request)

        if (
            managed_id is not None
            and path in _START_PATHS
            and 200 <= int(getattr(response, "status_code", 500)) < 300
        ):
            try:
                _clear_stop_sentinel(managed_id)
            except Exception:
                pass
        return response

    # Replace only the direct Stop + status endpoints.  Session/arm/heartbeat stay
    # on the reviewed direct-execution control plane.
    _remove_route(app, "/me/direct-execution/stop", "POST")
    _remove_route(app, "/me/direct-execution/status", "GET")

    @app.post("/me/direct-execution/stop")
    def hard_stop_direct_execution(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        managed_id = _current_managed_id(request)

        # This is the financial commit.  It uses a dedicated RuntimePreference key
        # that the worker does not otherwise lock, so Stop is not serialized behind
        # account refresh/proposal work.
        _write_stop_sentinel(managed_id, "User pressed Stop")

        # UI can change to Start immediately.  Slow lifecycle-row cleanup happens
        # after the HTTP response and cannot reopen financial execution.
        background_tasks.add_task(_normalize_stopped_account, managed_id)
        try:
            account = base_api.get_current_account(request) or {}
            base_api.mark_dashboard_dirty(account.get("account_type"))
        except Exception:
            pass
        return {
            "success": True,
            "state": "stopped",
            "lifecycle": "stopped",
            "enabled": False,
            "hard_stop": True,
            "purchase_allowed": False,
        }

    @app.get("/me/direct-execution/status")
    def hard_stop_aware_status(request: Request) -> JSONResponse:
        managed_id = _current_managed_id(request)
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")
            if direct_hard_stop_active(session, managed_id):
                return JSONResponse(
                    {
                        "authenticated": True,
                        "owner": "stopped",
                        "epoch": "",
                        "execution_status": "stopped",
                        "enabled": False,
                        "lease_remaining_seconds": 0.0,
                        "hard_stop": True,
                        "purchase_allowed": False,
                    },
                    headers={"Cache-Control": "no-store"},
                )

            owner_row = session.get(RuntimePreference, f"{_OWNER_PREFIX}{managed_id}")
            owner_epoch = ""
            if owner_row is not None:
                try:
                    import json

                    payload = json.loads(str(owner_row.preference_value or "{}"))
                    if isinstance(payload, dict):
                        owner_epoch = str(payload.get("epoch") or "")
                except Exception:
                    owner_epoch = ""

            remaining = direct_browser_lease_remaining_seconds(row)
            status = str(row.execution_status or "inactive").strip().lower()
            if status == DIRECT_BROWSER_STATUS and remaining > 0 and bool(row.enabled):
                owner = "browser"
            elif bool(row.enabled):
                owner = "server_takeover" if status == DIRECT_BROWSER_STATUS else "server"
            else:
                owner = "stopped"

            payload = {
                "authenticated": True,
                "owner": owner,
                "epoch": owner_epoch,
                "execution_status": status,
                "enabled": bool(row.enabled),
                "lease_remaining_seconds": round(float(remaining), 3),
                "hard_stop": False,
                "purchase_allowed": bool(row.enabled),
            }
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    app.state.direct_hard_stop_v2_installed = True
    _INSTALLED = True
