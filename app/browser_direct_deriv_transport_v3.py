from __future__ import annotations

"""Browser-direct Deriv transport v3.

Live/manual Options trading is intentionally NOT a VPS transport. The browser uses
one short-lived OAuth access token from the existing login to call Deriv's official
OTP REST endpoint directly, then opens the returned authenticated Deriv WebSocket
and performs proposal/BUY/contract subscriptions there.

The VPS is a light control plane only:

* one credential bootstrap when a page/start needs a fresh OAuth access token;
* one Start/Arm persistence mutation;
* explicit account-global Stop and Clear through the existing authorities; and
* two small trade receipts per real contract (OPEN and SETTLED).

There is no server OTP generation, browser ownership heartbeat write, live-browser
VPS takeover, proposal relay, BUY relay, tick relay, balance relay, or per-tick DB
work in this authority. Refresh tokens never leave the server.
"""

import json
import logging
from typing import Any, Callable

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

import app.api as base_api
import app.vps_cross_device_runtime_sync as cross_sync
import app.vps_direct_execution_api as direct_api
from app.direct_execution_hard_stop_state import (
    clear_direct_hard_stop,
    direct_hard_stop_active,
)
from app.direct_execution_lease import DIRECT_BROWSER_STATUS
from app.models import ManagedAccount, RuntimePreference, Trade, utc_now
from app.oauth_direct_account_authority import oauth_trade_access_token


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_OWNER_PREFIX = "direct_execution:v1:"
_RECEIPT_PREFIX = "browser_trade_receipts:v3:"
_MAX_RECEIPTS = 120


class DirectBootstrapRequest(BaseModel):
    force_refresh: bool = False


class DirectTradeReceipt(BaseModel):
    event: str = Field(min_length=3, max_length=16)
    contract_id: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


def _remove_route(app: Any, path: str, method: str) -> Callable[..., Any] | None:
    expected = str(method).upper()
    captured: Callable[..., Any] | None = None
    retained = []
    for route in app.router.routes:
        if (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        ):
            endpoint = getattr(route, "endpoint", None)
            if callable(endpoint):
                captured = endpoint
            continue
        retained.append(route)
    app.router.routes[:] = retained
    return captured


def _current_account(request: Request) -> dict[str, Any]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return account


def _receipt_key(managed_id: int) -> str:
    return f"{_RECEIPT_PREFIX}{int(managed_id)}"


def _owner_key(managed_id: int) -> str:
    return f"{_OWNER_PREFIX}{int(managed_id)}"


def _safe_json(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _read_receipts(session: Any, managed_id: int) -> list[dict[str, Any]]:
    row = session.get(RuntimePreference, _receipt_key(managed_id))
    if row is None:
        return []
    value = _safe_json(row.preference_value, [])
    return value if isinstance(value, list) else []


def _write_receipts(session: Any, managed_id: int, rows: list[dict[str, Any]]) -> None:
    key = _receipt_key(managed_id)
    value = json.dumps(rows[-_MAX_RECEIPTS:], separators=(",", ":"), sort_keys=True)
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=value))
    else:
        row.preference_value = value
        row.updated_at = utc_now()


def _clear_receipts(managed_id: int) -> None:
    with base_api.DATABASE.session() as session:
        row = session.get(RuntimePreference, _receipt_key(managed_id))
        if row is not None:
            session.delete(row)


def _safe_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _receipt_payload(event: str, contract_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    allowed_strings = {
        "mode",
        "state",
        "symbol",
        "market",
        "trade_type",
        "contract_type",
        "outcome",
        "at",
        "opened_at",
        "purchase_time",
        "settlement_time",
    }
    allowed_numbers = {
        "prediction",
        "barrier",
        "stake",
        "buy_price",
        "payout",
        "profit",
        "session_profit",
        "entry_spot",
        "exit_spot",
        "entry_digit",
        "actual_last_digit",
        "exit_digit",
    }
    payload: dict[str, Any] = {
        "event": event,
        "contract_id": contract_id,
        "received_at": utc_now().isoformat(),
    }
    for key in allowed_strings:
        if key not in raw or raw.get(key) is None:
            continue
        payload[key] = str(raw.get(key))[:120]
    for key in allowed_numbers:
        if key not in raw:
            continue
        number = _safe_number(raw.get(key))
        if number is not None:
            payload[key] = number
    return payload


def _latest_browser_purchase(receipts: list[dict[str, Any]]) -> str:
    for row in reversed(receipts):
        if str(row.get("event") or "").upper() != "OPEN":
            continue
        return str(
            row.get("opened_at")
            or row.get("purchase_time")
            or row.get("at")
            or row.get("received_at")
            or ""
        )
    return ""


def install_browser_direct_deriv_transport_v3(app: Any) -> None:
    """Install the final live/manual browser-direct transport authority."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Cross-device v2 used to wrap the server-side OTP helper with a 45-second
    # retry loop. Browser-direct v3 never calls that helper for live trading. Put
    # the original function back so an old cached client cannot create a nested
    # server retry storm while it is being phased out.
    original_provider_otp = getattr(cross_sync, "_ORIGINAL_PROVIDER_OTP", None)
    if callable(original_provider_otp):
        direct_api._provider_otp = original_provider_otp

    for path, method in (
        ("/me/direct-execution/session", "POST"),
        ("/me/direct-execution/arm", "POST"),
        ("/me/direct-execution/heartbeat", "POST"),
        ("/me/direct-execution/yield", "POST"),
        ("/me/direct-execution/status", "GET"),
        ("/me/runtime-sync", "GET"),
    ):
        _remove_route(app, path, method)

    @app.post("/me/direct-execution/bootstrap")
    def browser_direct_bootstrap(
        request: Request,
        body: DirectBootstrapRequest,
    ) -> JSONResponse:
        """Return only the short-lived trade-scoped OAuth access token.

        The browser uses this token directly against api.derivws.com. The refresh
        token remains encrypted on the VPS and is never returned to JavaScript.
        """

        account = _current_account(request)
        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            row = direct_api._managed_row(session, managed_id, for_update=True)
            payload = direct_api._fresh_oauth_payload(
                session,
                row,
                direct_api._auth_payload(row),
                force=bool(body.force_refresh),
            )
            token = str(oauth_trade_access_token(payload) or "").strip()
            if not token:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This Deriv login does not contain a short-lived trade-scoped OAuth access token. "
                        "Sign in to Deriv again to renew browser-direct trading authorization."
                    ),
                )
            access_key, _refresh_key, expires_key, scope_key = direct_api._oauth_fields(payload)
            scope = str(payload.get(scope_key) or "").strip()
            if "trade" not in set(scope.replace(",", " ").split()):
                raise HTTPException(status_code=403, detail="Deriv OAuth trade scope is required")
            account_id = str(payload.get("account_id") or account.get("account_id") or "").strip()
            if not account_id:
                raise HTTPException(status_code=409, detail="Deriv account ID is unavailable")
            expires_at = str(payload.get(expires_key) or "").strip()
            # Defensive assertion: never accidentally expose refresh credentials.
            if access_key and not str(payload.get(access_key) or "").strip():
                raise HTTPException(status_code=409, detail="Deriv OAuth access token is unavailable")

        response = JSONResponse(
            {
                "success": True,
                "access_token": token,
                "token_type": "Bearer",
                "expires_at": expires_at,
                "scope": scope,
                "account_id": account_id,
                "account_type": str(account.get("account_type") or payload.get("account_type") or "demo"),
                "deriv_app_id": str(getattr(base_api.CONFIG.deriv, "app_id", "") or base_api.oauth_client_id()),
                "api_base": "https://api.derivws.com",
                "transport": "browser_deriv_direct_v3",
                "server_otp": False,
                "server_proposal": False,
                "server_buy": False,
                "refresh_token_exposed": False,
            },
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, private",
                "Pragma": "no-cache",
            },
        )
        return response

    @app.post("/me/direct-execution/session")
    def retired_server_session(request: Request) -> JSONResponse:
        _current_account(request)
        return JSONResponse(
            {
                "success": False,
                "detail": "Server OTP transport is retired. Refresh the application to use browser-direct Deriv v3.",
                "transport": "browser_deriv_direct_v3",
                "server_otp": False,
            },
            status_code=410,
            headers={"Cache-Control": "no-store", "Retry-After": "60"},
        )

    @app.post("/me/direct-execution/arm")
    def arm_browser_direct(request: Request, body: direct_api.DirectArmRequest) -> dict[str, Any]:
        """One write at Start; no lease heartbeat is required afterward."""

        account = _current_account(request)
        managed_id = int(account["id"])
        now = utc_now()
        with base_api.DATABASE.session() as session:
            row = direct_api._managed_row(session, managed_id, for_update=True)
            strategy = dict(body.strategy or {})
            if strategy:
                direct_api._reset_risk_state_bounded(session, managed_id)
                direct_api._delete_runtime_preferences_bounded(session, managed_id)
                config = direct_api.write_custom_strategy(session, managed_id, strategy)
                direct_api.write_strategy(
                    session,
                    managed_id,
                    family="custom",
                    side="custom",
                    prediction=None,
                )
                martingale = strategy.get("martingale")
                if isinstance(martingale, dict):
                    direct_api._write_custom_martingale(session, managed_id, martingale)
                direct_api._apply_execution_settings(row, strategy)
            else:
                config = direct_api.read_custom_strategy(base_api.DATABASE, managed_id)
                if not bool(config.get("configured")):
                    raise HTTPException(status_code=409, detail="Save a strategy before starting execution")

            # Explicit Start is the only action that clears an earlier manual Stop.
            clear_direct_hard_stop(session, managed_id)
            row.enabled = True
            row.execution_status = DIRECT_BROWSER_STATUS
            row.execution_status_reason = (
                "Browser executes directly with Deriv; VPS receives control events and trade receipts only."
            )[:160]
            row.execution_status_updated_at = now
            row.updated_at = now
            direct_api._write_owner_preference(
                session,
                managed_id,
                {
                    "epoch": body.epoch,
                    "owner": "browser_direct_only",
                    "armed_at": now.isoformat(),
                    "transport": "browser_deriv_direct_v3",
                    "heartbeat_required": False,
                    "server_takeover": False,
                },
            )

        try:
            base_api.mark_dashboard_dirty(account.get("account_type"))
        except Exception:
            pass
        return {
            "success": True,
            "owner": "browser",
            "epoch": body.epoch,
            # Kept large for a briefly cached v2 financial fence. The v3 browser
            # fence does not use a time lease at all.
            "lease_seconds": 3600,
            "heartbeat_seconds": 0,
            "heartbeat_required": False,
            "offline_takeover": False,
            "transport": "browser_deriv_direct_v3",
        }

    @app.post("/me/direct-execution/heartbeat")
    def compatibility_heartbeat(
        request: Request,
        body: direct_api.DirectHeartbeatRequest,
    ) -> dict[str, Any]:
        # Compatibility only for a tab that has not yet hard-refreshed. Deliberately
        # no DB write and no provider call.
        _current_account(request)
        return {
            "success": True,
            "owner": "browser",
            "epoch": body.epoch,
            "lease_seconds": 3600,
            "heartbeat_required": False,
            "transport": "browser_deriv_direct_v3",
        }

    @app.post("/me/direct-execution/yield")
    def retired_takeover(
        request: Request,
        body: direct_api.DirectYieldRequest,
    ) -> dict[str, Any]:
        # Browser channel faults are repaired browser->Deriv. They never wake VPS
        # proposal/BUY infrastructure in v3.
        _current_account(request)
        return {
            "success": True,
            "owner": "browser",
            "epoch": body.epoch,
            "enabled": True,
            "takeover_requested": False,
            "auto_trading_continues": True,
            "server_trade_transport": False,
            "transport": "browser_deriv_direct_v3",
        }

    @app.post("/me/direct-execution/receipt")
    def receive_browser_trade(
        request: Request,
        body: DirectTradeReceipt,
    ) -> dict[str, Any]:
        """Receive one OPEN or SETTLED receipt; never contact Deriv."""

        account = _current_account(request)
        managed_id = int(account["id"])
        event = str(body.event or "").strip().upper()
        if event not in {"OPEN", "SETTLED"}:
            raise HTTPException(status_code=400, detail="receipt event must be OPEN or SETTLED")
        contract_id = str(body.contract_id or "").strip()
        row = _receipt_payload(event, contract_id, dict(body.payload or {}))

        with base_api.DATABASE.session() as session:
            receipts = _read_receipts(session, managed_id)
            key = (event, contract_id)
            replaced = False
            for index, current in enumerate(receipts):
                if (
                    str(current.get("event") or "").upper(),
                    str(current.get("contract_id") or ""),
                ) == key:
                    receipts[index] = row
                    replaced = True
                    break
            if not replaced:
                receipts.append(row)
            _write_receipts(session, managed_id, receipts)

        return {
            "success": True,
            "received": event,
            "contract_id": contract_id,
            "provider_contacted": False,
        }

    @app.get("/me/direct-execution/receipts")
    def browser_trade_receipts(request: Request) -> JSONResponse:
        account = _current_account(request)
        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            receipts = _read_receipts(session, managed_id)
        return JSONResponse(
            {"success": True, "receipts": receipts[-_MAX_RECEIPTS:]},
            headers={"Cache-Control": "no-store, private"},
        )

    @app.get("/me/direct-execution/status")
    def browser_direct_status(request: Request) -> dict[str, Any]:
        account = _current_account(request)
        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            row = direct_api._managed_row(session, managed_id)
            hard_stop = bool(direct_hard_stop_active(session, managed_id))
            status = str(row.execution_status or "inactive").strip().lower()
        stopped = hard_stop or not bool(row.enabled) or status in {
            "take_profit",
            "stop_loss",
            "stopped",
            "manual_pause",
        }
        return {
            "authenticated": True,
            "owner": "stopped" if stopped else ("browser" if status == DIRECT_BROWSER_STATUS else "server"),
            "execution_status": status,
            "enabled": bool(row.enabled),
            "hard_stop": hard_stop,
            "lease_remaining_seconds": 0,
            "heartbeat_required": False,
            "server_takeover": False,
            "transport": "browser_deriv_direct_v3",
        }

    @app.get("/me/runtime-sync", include_in_schema=False)
    def browser_direct_runtime_sync(request: Request) -> JSONResponse:
        account = _current_account(request)
        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")
            hard_stop = bool(direct_hard_stop_active(session, managed_id))
            status = str(row.execution_status or "inactive").strip().lower()
            reason = str(row.execution_status_reason or "")[:160]
            owner_payload = _safe_json(
                getattr(session.get(RuntimePreference, _owner_key(managed_id)), "preference_value", ""),
                {},
            )
            receipts = _read_receipts(session, managed_id)
            browser_purchase = _latest_browser_purchase(receipts)
            latest_trade = None
            if not browser_purchase:
                latest_trade = session.scalar(
                    select(Trade)
                    .where(Trade.managed_account_id == managed_id)
                    .order_by(Trade.purchase_time.desc())
                    .limit(1)
                )

            if hard_stop or status in {"take_profit", "stop_loss"} or not bool(row.enabled):
                owner = "stopped"
            elif status == DIRECT_BROWSER_STATUS:
                owner = "browser"
            else:
                owner = "server"
            purchase_allowed = bool(
                row.enabled
                and not hard_stop
                and status not in {"take_profit", "stop_loss", "stopped", "manual_pause"}
            )
            clear_revision = ""
            try:
                clear_revision = cross_sync._read_clear_revision(session, managed_id)
            except Exception:
                clear_revision = ""
            last_purchase_at = browser_purchase
            if not last_purchase_at and latest_trade is not None and latest_trade.purchase_time is not None:
                last_purchase_at = latest_trade.purchase_time.isoformat()

        return JSONResponse(
            {
                "authenticated": True,
                "managed_account_id": managed_id,
                "owner": owner,
                "epoch": str(owner_payload.get("epoch") or ""),
                "enabled": bool(row.enabled),
                "execution_status": status,
                "execution_status_reason": reason,
                "lease_remaining_seconds": 0,
                "hard_stop": hard_stop,
                "purchase_allowed": purchase_allowed,
                "history_revision": clear_revision,
                "last_purchase_at": last_purchase_at,
                "updated_at": (
                    row.execution_status_updated_at.isoformat()
                    if row.execution_status_updated_at is not None
                    else ""
                ),
                "transport": "browser_deriv_direct_v3",
                "heartbeat_required": False,
                "server_takeover": False,
                "server_provider_requests": False,
            },
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, private"},
        )

    @app.middleware("http")
    async def clear_browser_receipts_with_history(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = str(request.url.path or "")
        clear_request = str(request.method or "GET").upper() == "POST" and path in {
            "/me/clear-trades",
            "/api/me/clear-trades",
        }
        managed_id: int | None = None
        if clear_request:
            try:
                account = base_api.get_current_account(request)
                if account:
                    managed_id = int(account["id"])
            except Exception:
                managed_id = None
        response = await call_next(request)
        if (
            clear_request
            and managed_id is not None
            and 200 <= int(getattr(response, "status_code", 500)) < 300
        ):
            try:
                _clear_receipts(managed_id)
            except Exception:
                LOGGER.exception("BROWSER_DIRECT_RECEIPT_CLEAR_FAILED managed_id=%s", managed_id)
        return response

    app.state.browser_direct_deriv_transport_v3_installed = True
    app.state.live_manual_transport = "browser_deriv_direct_v3"
    app.state.live_server_provider_requests = False
    app.state.live_server_otp = False
    app.state.live_server_proposal = False
    app.state.live_server_buy = False
    app.state.live_server_takeover = False
    app.state.browser_trade_receipts = "open_and_settled_only"
    _INSTALLED = True
