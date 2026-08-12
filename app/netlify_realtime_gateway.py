from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

import app.api as base_api
from app.api_performance_hardening import (
    _build_current_account,
    _fast_trade_payload,
    _me_payload,
)
from app.custom_strategy_runtime_api import _runtime_state
from app.dashboard_live_events import _live_snapshot
from app.dashboard_stability_fix import _remove_route
from app.models import ClientSession


_INSTALLED = False
_TICKET_TTL_SECONDS = 45
_FALLBACK_REVISION_SECONDS = 2.0
_HEARTBEAT_SECONDS = 12.0


class _RealtimeHub:
    """Process-local wake-up fanout for dashboard WebSocket connections."""

    def __init__(self) -> None:
        self._generation = 0
        self._condition = asyncio.Condition()

    @property
    def generation(self) -> int:
        return int(self._generation)

    async def publish(self) -> int:
        async with self._condition:
            self._generation += 1
            self._condition.notify_all()
            return int(self._generation)

    async def wait_after(self, generation: int, timeout: float) -> int:
        async with self._condition:
            if self._generation != int(generation):
                return int(self._generation)
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: self._generation != int(generation)
                    ),
                    timeout=max(0.1, float(timeout)),
                )
            except asyncio.TimeoutError:
                pass
            return int(self._generation)


_HUB = _RealtimeHub()


def _urlsafe_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    padding = "=" * (-len(str(value)) % 4)
    return base64.urlsafe_b64decode((str(value) + padding).encode("ascii"))


def _ticket_secret() -> bytes:
    value = (
        os.getenv("DASHBOARD_STREAM_SIGNING_KEY", "").strip()
        or str(base_api.CONFIG.deriv.token_encryption_key or "").strip()
        or os.getenv("CONTROL_API_KEY", "").strip()
    )
    if len(value) < 24:
        raise RuntimeError(
            "DASHBOARD_STREAM_SIGNING_KEY must be configured with at least 24 characters"
        )
    return value.encode("utf-8")


def _encode_ticket(payload: dict[str, Any], secret: bytes) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = _urlsafe_encode(body)
    signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_urlsafe_encode(signature)}"


def _decode_ticket(token: str, secret: bytes) -> dict[str, Any]:
    try:
        encoded, supplied = str(token or "").split(".", 1)
        expected = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
        actual = _urlsafe_decode(supplied)
        if not hmac.compare_digest(expected, actual):
            raise ValueError("signature")
        payload = json.loads(_urlsafe_decode(encoded).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload")
    except Exception as exc:
        raise ValueError("invalid realtime ticket") from exc
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("expired realtime ticket")
    return payload


def _session_hash_from_request(request: Request) -> str:
    token = str(request.cookies.get(base_api.CLIENT_SESSION_COOKIE, "") or "").strip()
    return base_api.session_hash(token) if token else ""


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _ticket_session_account(payload: dict[str, Any]) -> dict[str, Any] | None:
    session_hash_value = str(payload.get("sid") or "").strip()
    try:
        managed_id = int(payload.get("mid"))
    except (TypeError, ValueError):
        return None
    if not session_hash_value:
        return None
    with base_api.DATABASE.session() as session:
        client = session.get(ClientSession, session_hash_value)
        if client is None:
            return None
        if int(client.managed_account_id) != managed_id:
            return None
        if _aware(client.expires_at) <= datetime.now(timezone.utc):
            return None
    account = _build_current_account(session_hash_value)
    if not account or int(account.get("id") or 0) != managed_id:
        return None
    return account


def _frontend_origins() -> set[str]:
    raw = ",".join(
        value
        for value in (
            os.getenv("DASHBOARD_FRONTEND_ORIGINS", ""),
            os.getenv("CORS_ALLOWED_ORIGINS", ""),
        )
        if value
    )
    return {
        item.strip().rstrip("/")
        for item in raw.split(",")
        if item.strip()
    }


def _origin_allowed(origin: str) -> bool:
    value = str(origin or "").strip().rstrip("/")
    if not value:
        return False
    if value.startswith("http://localhost:") or value.startswith("http://127.0.0.1:"):
        return True
    allowed = _frontend_origins()
    return bool(allowed and value in allowed)


def _lifecycle_from_me(me: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(me.get("enabled"))
    status = str(me.get("execution_status") or "inactive").strip().lower()
    state = _runtime_state(enabled=enabled, status=status)
    return {
        "authenticated": True,
        "enabled": enabled,
        "runtime_state": state,
        "execution_status": status,
        "reason": str(me.get("execution_status_reason") or ""),
        "fatal": state == "ERROR",
    }


def _combined_snapshot(account: dict[str, Any]) -> dict[str, Any]:
    managed_id = int(account["id"])
    revision = _live_snapshot(managed_id) or {}
    me = _me_payload(account)
    trades = _fast_trade_payload(account, 100)
    lifecycle = _lifecycle_from_me(me)
    revision_value = str(revision.get("revision") or "")
    return {
        "type": "snapshot",
        "authenticated": True,
        "managed_account_id": managed_id,
        "revision": revision_value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "me": me,
        "lifecycle": lifecycle,
        "trades": {**trades, "revision": revision_value},
    }


def _snapshot_for_request(request: Request) -> dict[str, Any]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _combined_snapshot(account)


def _netlify_mode_enabled() -> bool:
    return os.getenv("FRONTEND_HOSTING_MODE", "vps_compat").strip().lower() == "netlify"


def install_netlify_realtime_gateway(app: Any) -> None:
    """Install the static-Netlify/frontend-to-VPS realtime boundary.

    REST and OAuth remain normal FastAPI routes and can be reached through a
    same-origin Netlify proxy. Realtime uses a short-lived signed ticket followed
    by a direct browser WebSocket to the backend, so the worker never depends on
    the browser or a frontend polling cycle.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    for path, method in (
        ("/me/live-ticket", "GET"),
        ("/me/live-snapshot", "GET"),
        ("/health/frontend-backend", "GET"),
        ("/control/internal/dashboard-settlement-refresh", "POST"),
    ):
        _remove_route(app, path, method)

    # The global model-summary builder is a historical VPS-dashboard feature. The
    # Netlify Custom Strategy frontend never consumes it, and production logs show
    # it can monopolize the API for tens of seconds. In split mode dashboard-dirty
    # calls therefore become cheap realtime wake-ups rather than aggregate rebuilds.
    if _netlify_mode_enabled():
        def netlify_mark_dashboard_dirty(_account_type: str | None = None) -> None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(_HUB.publish())

        base_api.mark_dashboard_dirty = netlify_mark_dashboard_dirty
        app.state.legacy_dashboard_summary_disabled = True

    @app.get("/me/live-ticket", include_in_schema=False)
    def netlify_live_ticket(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        session_hash_value = _session_hash_from_request(request)
        if not session_hash_value:
            raise HTTPException(status_code=401, detail="Server session is unavailable")
        now = int(time.time())
        ticket = _encode_ticket(
            {
                "sid": session_hash_value,
                "mid": int(account["id"]),
                "iat": now,
                "exp": now + _TICKET_TTL_SECONDS,
                "nonce": secrets.token_urlsafe(8),
            },
            _ticket_secret(),
        )
        return {
            "authenticated": True,
            "ticket": ticket,
            "expires_in": _TICKET_TTL_SECONDS,
            "transport": "direct_backend_websocket",
        }

    @app.get("/me/live-snapshot", include_in_schema=False)
    def netlify_live_snapshot(request: Request) -> dict[str, Any]:
        return _snapshot_for_request(request)

    @app.get("/health/frontend-backend", include_in_schema=False)
    def netlify_frontend_backend_health() -> dict[str, Any]:
        return {
            "status": "ready",
            "frontend": "netlify_static",
            "backend": "vps_api_worker_postgres",
            "rest_transport": "netlify_same_origin_proxy",
            "realtime_transport": "direct_signed_websocket",
            "browser_controls_worker_lifetime": False,
            "legacy_summary_disabled": bool(
                getattr(app.state, "legacy_dashboard_summary_disabled", False)
            ),
        }

    @app.post("/control/internal/dashboard-settlement-refresh", include_in_schema=False)
    async def realtime_worker_wakeup(
        _administrator: str = Depends(base_api.require_control_auth),
    ) -> dict[str, Any]:
        generation = await _HUB.publish()
        return {
            "accepted": True,
            "realtime_generation": generation,
            "legacy_summary_rebuild": False,
        }

    @app.websocket("/ws/me/live")
    async def netlify_live_websocket(websocket: WebSocket, ticket: str = "") -> None:
        if not _origin_allowed(websocket.headers.get("origin", "")):
            await websocket.close(code=4403, reason="Frontend origin is not allowed")
            return
        try:
            payload = _decode_ticket(ticket, _ticket_secret())
        except (ValueError, RuntimeError):
            await websocket.close(code=4401, reason="Realtime ticket is invalid")
            return
        account = await asyncio.to_thread(_ticket_session_account, payload)
        if not account:
            await websocket.close(code=4401, reason="Realtime session has expired")
            return

        managed_id = int(account["id"])
        await websocket.accept()
        generation = _HUB.generation
        last_revision = ""
        last_heartbeat = time.monotonic()
        try:
            while True:
                account = await asyncio.to_thread(_ticket_session_account, payload)
                if not account or int(account.get("id") or 0) != managed_id:
                    await websocket.close(code=4401, reason="Realtime session ended")
                    return

                revision = await asyncio.to_thread(_live_snapshot, managed_id)
                revision_value = str((revision or {}).get("revision") or "")
                if not last_revision or revision_value != last_revision:
                    snapshot = await asyncio.to_thread(_combined_snapshot, account)
                    last_revision = str(snapshot.get("revision") or revision_value)
                    await websocket.send_json(snapshot)
                    last_heartbeat = time.monotonic()

                next_generation = await _HUB.wait_after(
                    generation,
                    _FALLBACK_REVISION_SECONDS,
                )
                if next_generation != generation:
                    generation = next_generation
                    # Force an immediate revision check on the next loop.
                    continue

                if time.monotonic() - last_heartbeat >= _HEARTBEAT_SECONDS:
                    await websocket.send_json({"type": "heartbeat", "ts": time.time()})
                    last_heartbeat = time.monotonic()
        except WebSocketDisconnect:
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            base_api.LOGGER.exception(
                "NETLIFY_REALTIME_WEBSOCKET_FAILED managed_id=%s",
                managed_id,
            )
            try:
                await websocket.close(code=1011, reason="Realtime connection restarting")
            except Exception:
                pass

    app.state.netlify_realtime_gateway_installed = True
    app.state.frontend_architecture = "netlify-static-vps-backend-v1"
    _INSTALLED = True
