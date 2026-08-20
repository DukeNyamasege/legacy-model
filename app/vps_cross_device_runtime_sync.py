from __future__ import annotations

"""Account-global runtime synchronization for the Full-VPS browser-direct runtime.

This authority has four narrow jobs:

* expose one no-cache account runtime snapshot that every logged-in device can poll;
* publish a monotonic clear-history revision after a successful Clear Trades so all
  devices clear the same account ledger;
* preserve Stop/TP/SL as account-global state rather than browser-local state; and
* keep transient direct-session OTP/bootstrap failures retryable without changing
  Auto Trading lifecycle state.

It never submits a proposal or BUY and never auto-starts an idle account.
"""

import json
import logging
import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

import app.api as base_api
import app.vps_direct_execution_api as direct_api
from app.direct_execution_hard_stop_state import direct_hard_stop_active
from app.direct_execution_lease import (
    DIRECT_BROWSER_STATUS,
    direct_browser_lease_remaining_seconds,
)
from app.models import ManagedAccount, RuntimePreference, utc_now


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_CLEAR_PREFIX = "run_history_revision:v1:"
_OWNER_PREFIX = "direct_execution:v1:"
_TRANSIENT_SESSION_RETRY_SECONDS = 45.0
_ORIGINAL_PROVIDER_OTP: Any = None


def _clear_key(managed_id: int) -> str:
    return f"{_CLEAR_PREFIX}{int(managed_id)}"


def _owner_key(managed_id: int) -> str:
    return f"{_OWNER_PREFIX}{int(managed_id)}"


def _json_payload(row: RuntimePreference | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        payload = json.loads(str(row.preference_value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _current_account(request: Request) -> dict[str, Any]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return account


def _write_clear_revision(managed_id: int) -> str:
    revision = utc_now().isoformat()
    payload = json.dumps(
        {"revision": revision, "cleared_at": revision},
        sort_keys=True,
        separators=(",", ":"),
    )
    with base_api.DATABASE.session() as session:
        key = _clear_key(managed_id)
        row = session.get(RuntimePreference, key, with_for_update=True)
        if row is None:
            session.add(RuntimePreference(preference_key=key, preference_value=payload))
        else:
            row.preference_value = payload
            row.updated_at = utc_now()
    return revision


def _read_clear_revision(session: Any, managed_id: int) -> str:
    payload = _json_payload(session.get(RuntimePreference, _clear_key(managed_id)))
    return str(payload.get("revision") or "")


def _resilient_provider_otp(account_id: str, token: str) -> str:
    """Retry only transient direct-session bootstrap failures for up to 45 seconds.

    Permanent credential/provider rejections are returned immediately so the
    browser Journal can show the exact error. Auto Trading remains enabled and the
    browser's own reconnect loop may retry after the user/provider condition changes.
    """

    original = _ORIGINAL_PROVIDER_OTP
    if original is None:
        raise HTTPException(status_code=503, detail="Direct Deriv session bootstrap is unavailable")

    deadline = time.monotonic() + _TRANSIENT_SESSION_RETRY_SECONDS
    attempt = 0
    last_error: HTTPException | None = None
    while True:
        attempt += 1
        try:
            return str(original(account_id, token))
        except HTTPException as exc:
            last_error = exc
            # 502/503 are transport/provider availability faults. 4xx credential
            # faults are useful exact diagnostics and should not be hidden behind
            # another 45 seconds of identical calls.
            if int(exc.status_code) not in {502, 503}:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(
                    status_code=503,
                    detail=str(exc.detail or "Authenticated Deriv session is temporarily unavailable"),
                ) from exc
            sleep_seconds = min(4.0, max(0.4, 0.6 * (2 ** min(attempt - 1, 3))))
            time.sleep(min(sleep_seconds, remaining))
        except Exception as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(
                    status_code=503,
                    detail=f"Authenticated Deriv session recovery failed: {type(exc).__name__}",
                ) from exc
            time.sleep(min(2.0, remaining))

    if last_error is not None:  # pragma: no cover - loop exits by return/raise
        raise last_error


def install_vps_cross_device_runtime_sync(app: Any) -> None:
    global _INSTALLED, _ORIGINAL_PROVIDER_OTP
    if _INSTALLED:
        return

    # The direct-session route resolves this module global at request time, so
    # replacing it here hardens browser bootstrap without duplicating the route.
    _ORIGINAL_PROVIDER_OTP = direct_api._provider_otp
    direct_api._provider_otp = _resilient_provider_otp

    @app.middleware("http")
    async def account_clear_revision_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = str(request.url.path or "")
        method = str(request.method or "GET").upper()
        managed_id: int | None = None
        account_type: str | None = None
        clear_request = method == "POST" and path in {
            "/me/clear-trades",
            "/api/me/clear-trades",
        }
        if clear_request:
            try:
                account = base_api.get_current_account(request)
                if account:
                    managed_id = int(account["id"])
                    account_type = str(account.get("account_type") or "")
            except Exception:
                managed_id = None

        response = await call_next(request)

        if (
            clear_request
            and managed_id is not None
            and 200 <= int(getattr(response, "status_code", 500)) < 300
        ):
            try:
                revision = _write_clear_revision(managed_id)
                base_api.mark_dashboard_dirty(account_type)
                LOGGER.info(
                    "ACCOUNT_HISTORY_CLEAR_REVISION managed_id=%s revision=%s cross_device=true",
                    managed_id,
                    revision,
                )
            except Exception:
                # The clear itself already succeeded. A later clear/snapshot can
                # repair synchronization; never turn history clearing into an
                # execution lifecycle fault.
                LOGGER.exception(
                    "ACCOUNT_HISTORY_CLEAR_REVISION_FAILED managed_id=%s",
                    managed_id,
                )
        return response

    @app.get("/me/runtime-sync", include_in_schema=False)
    def account_runtime_sync(request: Request) -> JSONResponse:
        account = _current_account(request)
        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")

            hard_stop = bool(direct_hard_stop_active(session, managed_id))
            status = str(row.execution_status or "inactive").strip().lower()
            reason = str(row.execution_status_reason or "")
            remaining = direct_browser_lease_remaining_seconds(row)
            owner_payload = _json_payload(session.get(RuntimePreference, _owner_key(managed_id)))

            if hard_stop or status in {"take_profit", "stop_loss"} or not bool(row.enabled):
                owner = "stopped"
            elif status == DIRECT_BROWSER_STATUS and remaining > 0:
                owner = "browser"
            elif status == DIRECT_BROWSER_STATUS:
                owner = "server_takeover"
            else:
                owner = "server"

            purchase_allowed = bool(
                row.enabled
                and not hard_stop
                and status not in {"take_profit", "stop_loss", "stopped", "manual_pause"}
            )
            payload = {
                "authenticated": True,
                "managed_account_id": managed_id,
                "owner": owner,
                "epoch": str(owner_payload.get("epoch") or ""),
                "enabled": bool(row.enabled),
                "execution_status": status,
                "execution_status_reason": reason,
                "lease_remaining_seconds": round(float(remaining), 3),
                "hard_stop": hard_stop,
                "purchase_allowed": purchase_allowed,
                "history_revision": _read_clear_revision(session, managed_id),
                "updated_at": (
                    row.execution_status_updated_at.isoformat()
                    if row.execution_status_updated_at is not None
                    else ""
                ),
            }

        return JSONResponse(
            payload,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, private"},
        )

    app.state.vps_cross_device_runtime_sync_installed = True
    app.state.runtime_stop_scope = "account_global_all_logged_in_devices"
    app.state.clear_history_scope = "account_global_all_logged_in_devices"
    app.state.direct_session_transient_retry_seconds = _TRANSIENT_SESSION_RETRY_SECONDS
    _INSTALLED = True
