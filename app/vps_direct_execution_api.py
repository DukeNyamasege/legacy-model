from __future__ import annotations

"""Thin control plane for browser-direct Deriv execution on the Full VPS.

Live/manual execution does not traverse the trading worker. The backend performs
only operations that cannot safely live in browser JavaScript:

* reuse and refresh the server-held OAuth/PAT credential from the user's Deriv login;
* exchange that credential for Deriv's required short-lived single-use WebSocket OTP;
* persist the strategy/risk snapshot used for offline server takeover;
* maintain a short browser ownership lease; and
* persist an explicit Stop so an offline worker may not later resume.

There is never a second user-facing authentication step. Deriv still requires an
OTP for each authenticated Options WebSocket, so this module obtains it silently
from the stored OAuth grant. No proposal, tick condition evaluation or BUY is
performed by these routes.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

import requests
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import app.api as base_api
from app.custom_strategy_api import _write_custom_martingale
from app.custom_strategy_v1 import read_custom_strategy, write_custom_strategy
from app.deriv.http import deriv_headers
from app.direct_execution_lease import (
    DIRECT_BROWSER_HEARTBEAT_SECONDS,
    DIRECT_BROWSER_LEASE_SECONDS,
    DIRECT_BROWSER_STATUS,
    direct_browser_lease_remaining_seconds,
)
from app.models import ManagedAccount, RuntimePreference, utc_now
from app.oauth_client import refresh_access_token, token_is_expiring
from app.oauth_direct_account_authority import oauth_trade_access_token
from app.strategy_v2_preferences import write_strategy
from app.token_store import decrypt_auth_payload, encrypt_auth_payload
from app.vps_fast_execution_controls import (
    _delete_runtime_preferences_bounded,
    _reset_risk_state_bounded,
)

_INSTALLED = False
PREFERENCE_PREFIX = "direct_execution:v1:"


class DirectArmRequest(BaseModel):
    epoch: str = Field(min_length=8, max_length=96)
    strategy: dict[str, Any] | None = None


class DirectHeartbeatRequest(BaseModel):
    epoch: str = Field(min_length=8, max_length=96)


class DirectYieldRequest(BaseModel):
    epoch: str = Field(min_length=8, max_length=96)
    reason: str | None = Field(default=None, max_length=180)


class DirectStopRequest(BaseModel):
    epoch: str | None = Field(default=None, max_length=96)


def _key(managed_id: int) -> str:
    return f"{PREFERENCE_PREFIX}{int(managed_id)}"


def _current_account(request: Request) -> dict[str, Any]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return account


def _managed_row(session: Any, managed_id: int, *, for_update: bool = False) -> ManagedAccount:
    row = session.get(ManagedAccount, int(managed_id), with_for_update=for_update)
    if row is None:
        raise HTTPException(status_code=401, detail="Managed account was not found")
    return row


def _auth_payload(row: ManagedAccount) -> dict[str, Any]:
    try:
        return decrypt_auth_payload(
            row.token_secret,
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Trading credential is unreadable") from exc


def _oauth_fields(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    if str(payload.get("auth_type") or "").strip().lower() == "oauth":
        return "access_token", "refresh_token", "expires_at", "scope"
    return "oauth_access_token", "oauth_refresh_token", "oauth_expires_at", "oauth_scope"


def _oauth_refresh_token(payload: dict[str, Any]) -> str:
    _access_key, refresh_key, _expires_key, _scope_key = _oauth_fields(payload)
    return str(payload.get(refresh_key) or "").strip()


def _apply_refreshed_oauth(payload: dict[str, Any], refreshed: dict[str, Any]) -> dict[str, Any]:
    updated = dict(payload)
    access_key, refresh_key, expires_key, scope_key = _oauth_fields(updated)
    if access_key == "access_token":
        account_id = str(updated.get("account_id") or "")
        account_type = str(updated.get("account_type") or "")
        auth_source = str(updated.get("auth_source") or "deriv_oauth")
        updated.update(refreshed)
        updated["auth_type"] = "oauth"
        if account_id:
            updated["account_id"] = account_id
        if account_type:
            updated["account_type"] = account_type
        updated["auth_source"] = auth_source
        return updated

    updated[access_key] = str(refreshed.get("access_token") or "").strip()
    updated[refresh_key] = str(
        refreshed.get("refresh_token") or updated.get(refresh_key) or ""
    ).strip()
    updated[expires_key] = str(refreshed.get("expires_at") or "").strip()
    updated[scope_key] = str(refreshed.get("scope") or updated.get(scope_key) or "").strip()
    return updated


def _persist_refreshed_oauth_for_login(
    session: Any,
    row: ManagedAccount,
    payload: dict[str, Any],
    refreshed: dict[str, Any],
    old_refresh_token: str,
) -> dict[str, Any]:
    """Persist one refreshed login grant across all linked rows from that login.

    A Deriv login may expose several Options accounts. Refresh-token rotation must
    not leave sibling accounts holding an obsolete refresh token, so the refresh is
    copied only to rows that still carry the exact pre-refresh token. Account IDs,
    account types and any PAT fallback remain account-local.
    """

    current = _apply_refreshed_oauth(payload, refreshed)
    row.token_secret = encrypt_auth_payload(
        current,
        base_api.CONFIG.deriv.token_encryption_key,
    )
    row.updated_at = utc_now()

    if not old_refresh_token:
        return current

    for peer in session.query(ManagedAccount).all():
        if int(peer.id) == int(row.id):
            continue
        try:
            peer_payload = decrypt_auth_payload(
                peer.token_secret,
                base_api.CONFIG.deriv.token_encryption_key,
            )
        except Exception:
            continue
        if _oauth_refresh_token(peer_payload) != old_refresh_token:
            continue
        peer_payload = _apply_refreshed_oauth(peer_payload, refreshed)
        peer.token_secret = encrypt_auth_payload(
            peer_payload,
            base_api.CONFIG.deriv.token_encryption_key,
        )
        peer.updated_at = utc_now()
    return current


def _fresh_oauth_payload(
    session: Any,
    row: ManagedAccount,
    payload: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Reuse the OAuth login and refresh it silently when required.

    This is credential maintenance, not another user authentication ceremony. A
    login is requested again only if Deriv has actually revoked/expired the refresh
    grant. Temporary network/provider failures remain retryable and never stop the
    account lifecycle.
    """

    access_key, refresh_key, expires_key, scope_key = _oauth_fields(payload)
    access_token = str(payload.get(access_key) or "").strip()
    refresh_token_value = str(payload.get(refresh_key) or "").strip()
    scope = str(payload.get(scope_key) or "").strip()
    oauth_backed = bool(access_token and "trade" in set(scope.replace(",", " ").split()))
    if not oauth_backed:
        return payload

    expiring = token_is_expiring({"expires_at": str(payload.get(expires_key) or "")})
    if not force and not expiring:
        return payload
    if not refresh_token_value:
        # A PAT fallback may still exist and _trade_credential() will use it. Do
        # not turn an absent OAuth refresh token into an automatic Stop.
        return payload

    try:
        refreshed = refresh_access_token(
            client_id=base_api.oauth_client_id(),
            refresh_token=refresh_token_value,
        )
    except requests.HTTPError as exc:
        status = int(exc.response.status_code) if exc.response is not None else 0
        if status in {400, 401, 403}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Deriv login authorization is expired or revoked. Sign in to Deriv again "
                    "to renew the trading grant."
                ),
            ) from exc
        raise HTTPException(
            status_code=503,
            detail="Deriv login token refresh is temporarily unavailable; automatic recovery will retry.",
        ) from exc
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail="Deriv login token refresh is temporarily unavailable; automatic recovery will retry.",
        ) from exc

    return _persist_refreshed_oauth_for_login(
        session,
        row,
        payload,
        refreshed,
        refresh_token_value,
    )


def _trade_credential(payload: dict[str, Any]) -> str:
    token = str(oauth_trade_access_token(payload) or "").strip()
    if not token:
        try:
            token = str(base_api.trading_api_token_from_payload(payload) or "").strip()
        except Exception:
            token = ""
    if not token:
        try:
            token = str(base_api.shared_trading_api_token(payload) or "").strip()
        except Exception:
            token = ""
    if not token:
        raise HTTPException(
            status_code=409,
            detail="The existing Deriv login does not contain a trade-scoped execution credential.",
        )
    return token


def _provider_otp(account_id: str, token: str) -> str:
    base_url = str(base_api.CONFIG.deriv.rest_base_url or "https://api.derivws.com").rstrip("/")
    url = f"{base_url}/trading/v1/options/accounts/{account_id}/otp"
    request = UrlRequest(
        url,
        data=b"",
        method="POST",
        headers=deriv_headers(base_api.CONFIG.deriv.app_id, bearer_token=token),
    )
    try:
        with urlopen(request, timeout=6.0) as response:  # nosec B310 - fixed configured Deriv origin
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        message = "Deriv rejected the direct-session request"
        try:
            provider_payload = json.loads(exc.read().decode("utf-8", errors="replace"))
            errors = provider_payload.get("errors") if isinstance(provider_payload, dict) else None
            if isinstance(errors, list) and errors:
                message = str((errors[0] or {}).get("message") or message)
        except Exception:
            pass
        status = 409 if exc.code in {400, 401, 403, 404} else 502
        raise HTTPException(status_code=status, detail=message) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Direct Deriv session is temporarily unavailable") from exc

    try:
        provider_payload = json.loads(raw or "{}")
        ws_url = str(((provider_payload.get("data") or {}).get("url")) or "").strip()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Deriv returned an invalid direct-session response") from exc
    if not ws_url.startswith("wss://api.derivws.com/"):
        raise HTTPException(status_code=502, detail="Deriv did not return a valid authenticated WebSocket URL")
    return ws_url


def _provider_rejected_login(detail: Any) -> bool:
    text = str(detail or "").lower()
    return any(
        marker in text
        for marker in (
            "unauthorized",
            "invalid token",
            "authentication credential",
            "authentication failed",
            "expired token",
            "missing authentication",
        )
    )


def _preference_payload(row: RuntimePreference | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(str(row.preference_value or "{}"))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _write_owner_preference(session: Any, managed_id: int, payload: dict[str, Any]) -> None:
    key = _key(managed_id)
    value = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=value))
    else:
        row.preference_value = value
        row.updated_at = utc_now()


def _apply_execution_settings(row: ManagedAccount, strategy: dict[str, Any]) -> None:
    settings = strategy.get("execution_settings")
    if not isinstance(settings, dict):
        return
    try:
        stake = round(float(settings.get("stake_amount")), 2)
        if stake > 0:
            row.stake_amount = stake
    except (TypeError, ValueError):
        pass
    try:
        take_profit = float(settings.get("take_profit") or 0.0)
        if take_profit >= 0:
            row.take_profit = take_profit
    except (TypeError, ValueError):
        pass
    try:
        stop_loss = float(settings.get("stop_loss") or 0.0)
        if stop_loss >= 0:
            row.stop_loss = stop_loss
    except (TypeError, ValueError):
        pass
    if "martingale_enabled" in settings:
        row.martingale_enabled = bool(settings.get("martingale_enabled"))


def install_vps_direct_execution_api(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    @app.post("/me/direct-execution/session")
    def direct_execution_session(request: Request) -> JSONResponse:
        account = _current_account(request)
        managed_id = int(account["id"])

        with base_api.DATABASE.session() as session:
            row = _managed_row(session, managed_id, for_update=True)
            payload = _fresh_oauth_payload(session, row, _auth_payload(row))
            account_id = str(payload.get("account_id") or account.get("account_id") or "").strip()
            if not account_id:
                raise HTTPException(status_code=409, detail="Deriv account ID is unavailable")
            token = _trade_credential(payload)

        try:
            ws_url = _provider_otp(account_id, token)
        except HTTPException as exc:
            # A token can be revoked/expired before its local expires_at. Refresh
            # once from the original Deriv login and retry the OTP automatically.
            if int(exc.status_code) != 409 or not _provider_rejected_login(exc.detail):
                raise
            with base_api.DATABASE.session() as session:
                row = _managed_row(session, managed_id, for_update=True)
                payload = _fresh_oauth_payload(session, row, _auth_payload(row), force=True)
                token = _trade_credential(payload)
            ws_url = _provider_otp(account_id, token)

        response = JSONResponse(
            {
                "success": True,
                "ws_url": ws_url,
                "expires_in": 120,
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "account_id_masked": str(account.get("account_id_masked") or ""),
                "account_type": str(account.get("account_type") or "demo"),
                "transport": "browser_direct_deriv_websocket",
                "authorization": "existing_deriv_login_reused",
                "second_login_required": False,
            }
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        return response

    @app.post("/me/direct-execution/arm")
    def arm_direct_execution(request: Request, body: DirectArmRequest) -> dict[str, Any]:
        account = _current_account(request)
        managed_id = int(account["id"])
        now = utc_now()
        with base_api.DATABASE.session() as session:
            row = _managed_row(session, managed_id, for_update=True)
            strategy = dict(body.strategy or {})
            if strategy:
                _reset_risk_state_bounded(session, managed_id)
                _delete_runtime_preferences_bounded(session, managed_id)
                config = write_custom_strategy(session, managed_id, strategy)
                write_strategy(
                    session,
                    managed_id,
                    family="custom",
                    side="custom",
                    prediction=None,
                )
                martingale = strategy.get("martingale")
                if isinstance(martingale, dict):
                    _write_custom_martingale(session, managed_id, martingale)
                _apply_execution_settings(row, strategy)
            else:
                config = read_custom_strategy(base_api.DATABASE, managed_id)
                if not bool(config.get("configured")):
                    raise HTTPException(status_code=409, detail="Save a strategy before starting direct execution")

            row.enabled = True
            row.execution_status = DIRECT_BROWSER_STATUS
            row.execution_status_reason = (
                "Browser owns live Deriv execution while its authenticated trade channel is healthy"
            )[:160]
            row.execution_status_updated_at = now
            row.updated_at = now
            _write_owner_preference(
                session,
                managed_id,
                {
                    "epoch": body.epoch,
                    "owner": "browser",
                    "armed_at": now.isoformat(),
                    "heartbeat_seconds": DIRECT_BROWSER_HEARTBEAT_SECONDS,
                    "lease_seconds": DIRECT_BROWSER_LEASE_SECONDS,
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
            "lease_seconds": DIRECT_BROWSER_LEASE_SECONDS,
            "heartbeat_seconds": DIRECT_BROWSER_HEARTBEAT_SECONDS,
            "offline_takeover": True,
        }

    @app.post("/me/direct-execution/heartbeat")
    def direct_execution_heartbeat(request: Request, body: DirectHeartbeatRequest) -> dict[str, Any]:
        account = _current_account(request)
        managed_id = int(account["id"])
        now = utc_now()
        with base_api.DATABASE.session() as session:
            row = _managed_row(session, managed_id, for_update=True)
            owner_row = session.get(RuntimePreference, _key(managed_id))
            owner = _preference_payload(owner_row)
            if str(owner.get("epoch") or "") != body.epoch:
                raise HTTPException(status_code=409, detail="Direct execution ownership changed")
            if not bool(row.enabled) or str(row.execution_status or "").strip().lower() != DIRECT_BROWSER_STATUS:
                raise HTTPException(status_code=409, detail="Direct execution is no longer armed")
            row.execution_status_updated_at = now
            row.updated_at = now
            owner["last_heartbeat_at"] = now.isoformat()
            _write_owner_preference(session, managed_id, owner)
        return {
            "success": True,
            "owner": "browser",
            "epoch": body.epoch,
            "lease_seconds": DIRECT_BROWSER_LEASE_SECONDS,
        }

    @app.post("/me/direct-execution/yield")
    def yield_direct_execution(request: Request, body: DirectYieldRequest) -> dict[str, Any]:
        """Yield an unhealthy browser lease without stopping Auto Trading.

        The row remains enabled and direct_browser. Its lease timestamp is expired
        atomically so the worker's existing 2-second takeover scanner performs the
        normal targeted admission/wake path. This is a handoff, never a Stop.
        """

        account = _current_account(request)
        managed_id = int(account["id"])
        now = utc_now()
        reason = str(body.reason or "Authenticated browser trade channel unavailable").strip()
        with base_api.DATABASE.session() as session:
            row = _managed_row(session, managed_id, for_update=True)
            status = str(row.execution_status or "").strip().lower()
            if not bool(row.enabled) or status in {"take_profit", "stop_loss", "stopped", "manual_pause"}:
                return {
                    "success": True,
                    "owner": "stopped",
                    "enabled": bool(row.enabled),
                    "takeover_requested": False,
                }

            owner_row = session.get(RuntimePreference, _key(managed_id))
            owner = _preference_payload(owner_row)
            owner_epoch = str(owner.get("epoch") or "")
            if owner_epoch and owner_epoch != body.epoch:
                raise HTTPException(status_code=409, detail="Direct execution ownership changed")

            if status == DIRECT_BROWSER_STATUS:
                row.execution_status_reason = (
                    f"Browser trade channel unavailable; VPS continuity takeover requested. {reason}"
                )[:160]
                row.execution_status_updated_at = now - timedelta(
                    seconds=DIRECT_BROWSER_LEASE_SECONDS + 1.0
                )
                row.updated_at = now
                owner.update(
                    {
                        "owner": "server_takeover",
                        "yielded_at": now.isoformat(),
                        "yield_reason": reason[:120],
                    }
                )
                _write_owner_preference(session, managed_id, owner)

        try:
            base_api.mark_dashboard_dirty(account.get("account_type"))
        except Exception:
            pass
        return {
            "success": True,
            "owner": "server_takeover",
            "enabled": True,
            "takeover_requested": True,
            "auto_trading_continues": True,
        }

    @app.post("/me/direct-execution/stop")
    def stop_direct_execution(request: Request, body: DirectStopRequest) -> dict[str, Any]:
        account = _current_account(request)
        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            row = _managed_row(session, managed_id, for_update=True)
            row.enabled = False
            row.execution_status = "stopped"
            row.execution_status_reason = (
                "Direct execution stopped. Browser and VPS are both forbidden from new purchases."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            owner_row = session.get(RuntimePreference, _key(managed_id))
            if owner_row is not None:
                session.delete(owner_row)
        try:
            base_api.mark_dashboard_dirty(account.get("account_type"))
        except Exception:
            pass
        return {
            "success": True,
            "state": "stopped",
            "lifecycle": "stopped",
            "enabled": False,
            "epoch": body.epoch,
        }

    @app.get("/me/direct-execution/status")
    def direct_execution_status(request: Request) -> dict[str, Any]:
        account = _current_account(request)
        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            row = _managed_row(session, managed_id)
            owner = _preference_payload(session.get(RuntimePreference, _key(managed_id)))
            remaining = direct_browser_lease_remaining_seconds(row)
            status = str(row.execution_status or "inactive").strip().lower()
            if status == DIRECT_BROWSER_STATUS and remaining <= 0 and bool(row.enabled):
                effective_owner = "server_takeover"
            elif status == DIRECT_BROWSER_STATUS and remaining > 0:
                effective_owner = "browser"
            elif bool(row.enabled):
                effective_owner = "server"
            else:
                effective_owner = "stopped"
        return {
            "authenticated": True,
            "owner": effective_owner,
            "epoch": str(owner.get("epoch") or ""),
            "execution_status": status,
            "enabled": bool(row.enabled),
            "lease_remaining_seconds": round(float(remaining), 3),
        }

    app.state.vps_direct_execution_api_installed = True
    app.state.live_execution_transport = "browser_direct_deriv_websocket"
    app.state.scheduled_execution_transport = "server_worker"
    app.state.oauth_login_reused_for_execution = True
    app.state.browser_unhealthy_yield_enabled = True
    _INSTALLED = True
