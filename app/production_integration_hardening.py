from __future__ import annotations

import asyncio
import copy
import hmac
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse

import app.api as base_api
import app.oauth_client as oauth_client


_INSTALLED = False
_RUNTIME_LOOP: asyncio.AbstractEventLoop | None = None
_DASHBOARD_EVENT: asyncio.Event | None = None


def _remove_routes(app: Any, paths: set[str]) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) not in paths
    ]


def _oauth_scopes() -> tuple[str, ...]:
    raw = os.getenv("DERIV_OAUTH_SCOPES", "trade application_read")
    scopes = tuple(dict.fromkeys(part for part in raw.replace(",", " ").split() if part))
    allowed = {"trade", "application_read", "account_manage"}
    unsupported = sorted(set(scopes) - allowed)
    if unsupported:
        raise RuntimeError(f"Unsupported DERIV_OAUTH_SCOPES: {unsupported}")
    if "trade" not in scopes:
        raise RuntimeError("DERIV_OAUTH_SCOPES must include trade")
    return scopes


def _validate_oauth_configuration() -> dict[str, Any]:
    redirect_uri = base_api.oauth_redirect_url()
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise RuntimeError(
            "DERIV_OAUTH_REDIRECT_URL must be one exact HTTPS callback URL without a query or fragment"
        )
    if parsed.path != "/oauth/callback":
        raise RuntimeError("DERIV_OAUTH_REDIRECT_URL must end with /oauth/callback")
    client_id = base_api.oauth_client_id()
    app_id = str(base_api.CONFIG.deriv.app_id or "").strip()
    if client_id != app_id:
        raise RuntimeError("Deriv OAuth client ID must match DERIV_APP_ID")
    scopes = _oauth_scopes()
    oauth_client.DEFAULT_SCOPES = scopes
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scopes": list(scopes),
    }


class ModeAwareDashboardHub:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, mode: str) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients[mode].add(websocket)

    async def disconnect(self, websocket: WebSocket, mode: str) -> None:
        async with self._lock:
            clients = self._clients.get(mode)
            if clients is not None:
                clients.discard(websocket)
                if not clients:
                    self._clients.pop(mode, None)

    async def has_clients(self, mode: str) -> bool:
        async with self._lock:
            return bool(self._clients.get(mode))

    async def broadcast(self, mode: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients.get(mode, set()))

        async def send(websocket: WebSocket) -> None:
            try:
                await asyncio.wait_for(websocket.send_json(payload), timeout=3.0)
            except Exception:
                await self.disconnect(websocket, mode)

        if clients:
            await asyncio.gather(*(send(client) for client in clients))


HUB = ModeAwareDashboardHub()


def install_production_integration_hardening(app: Any) -> None:
    """Install final production boundaries after every legacy API wrapper is loaded."""
    global _INSTALLED
    if _INSTALLED:
        return

    oauth_configuration = _validate_oauth_configuration()
    original_oauth_start = base_api.oauth_start
    original_oauth_callback = base_api.oauth_callback
    original_mark_dashboard_dirty = base_api.mark_dashboard_dirty
    verified_dashboard_summary = base_api.dashboard_summary

    def mark_dashboard_dirty_and_publish(account_type: str | None = None) -> None:
        original_mark_dashboard_dirty(account_type)
        loop = _RUNTIME_LOOP
        event = _DASHBOARD_EVENT
        if loop is not None and event is not None and not loop.is_closed():
            loop.call_soon_threadsafe(event.set)

    base_api.mark_dashboard_dirty = mark_dashboard_dirty_and_publish

    _remove_routes(
        app,
        {"/", "/oauth/start", "/oauth/callback", "/ws/dashboard", "/health/integration"},
    )

    def strict_oauth_callback(
        request: Request,
        *,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ) -> RedirectResponse:
        if error:
            return base_api.redirect_with_oauth_error(error_description or error)
        if not code or not state:
            return base_api.redirect_with_oauth_error("OAuth session is incomplete or expired")

        cookie_state = str(request.cookies.get(base_api.OAUTH_STATE_COOKIE, "") or "")
        if not cookie_state or not hmac.compare_digest(cookie_state, state):
            return base_api.redirect_with_oauth_error("OAuth state validation failed")

        stored_state = base_api.REPOSITORY.oauth_login_state(base_api.session_hash(state))
        if not stored_state:
            return base_api.redirect_with_oauth_error("OAuth session is incomplete, expired, or already used")

        cookie_verifier = str(
            request.cookies.get(base_api.OAUTH_VERIFIER_COOKIE, "") or ""
        )
        try:
            stored_verifier = base_api.decrypt_token(
                stored_state["code_verifier_secret"],
                base_api.CONFIG.deriv.token_encryption_key,
            )
        except Exception:
            stored_verifier = ""
        if (
            not cookie_verifier
            or not stored_verifier
            or not hmac.compare_digest(cookie_verifier, stored_verifier)
        ):
            return base_api.redirect_with_oauth_error("OAuth PKCE verification failed")

        stored_redirect = str(stored_state.get("redirect_uri") or "").strip()
        if not hmac.compare_digest(stored_redirect, oauth_configuration["redirect_uri"]):
            return base_api.redirect_with_oauth_error("OAuth redirect URI validation failed")

        return original_oauth_callback(
            request,
            code=code,
            state=state,
            error="",
            error_description="",
            landed_redirect_uri=stored_redirect,
        )

    @app.get("/", include_in_schema=False)
    def production_dashboard(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ):
        if code or error:
            return strict_oauth_callback(
                request,
                code=code,
                state=state,
                error=error,
                error_description=error_description,
            )
        html = (base_api.ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        scripts = (
            '<script src="/ui/account-lifecycle.js?v=20260731"></script>',
            '<script src="/ui/data-consistency.js?v=20260731"></script>',
            '<script src="/ui/security-hardening.js?v=20260731"></script>',
            '<script src="/ui/realtime-mode-hardening.js?v=20260731"></script>',
        )
        missing = [f"  {script}" for script in scripts if script not in html]
        if missing:
            html = html.replace("</body>", "\n".join(missing) + "\n</body>")
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
        )

    @app.get("/ui/realtime-mode-hardening.js", include_in_schema=False)
    def realtime_mode_hardening_script():
        from fastapi.responses import FileResponse

        return FileResponse(
            base_api.ROOT / "dashboard" / "realtime-mode-hardening.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/oauth/start")
    def production_oauth_start(request: Request) -> RedirectResponse:
        _validate_oauth_configuration()
        return original_oauth_start(request)

    @app.get("/oauth/callback")
    def production_oauth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ) -> RedirectResponse:
        return strict_oauth_callback(
            request,
            code=code,
            state=state,
            error=error,
            error_description=error_description,
        )

    @app.websocket("/ws/dashboard")
    async def production_dashboard_websocket(websocket: WebSocket) -> None:
        mode = base_api.normalize_account_type(websocket.query_params.get("mode", "demo"))
        await HUB.connect(websocket, mode)
        try:
            data = await asyncio.to_thread(verified_dashboard_summary, account_type=mode)
            await websocket.send_json({"type": "snapshot", "mode": mode, "data": data})
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await HUB.disconnect(websocket, mode)

    @app.get("/health/integration")
    def production_integration_health(
        _administrator: str = Depends(base_api.require_control_auth),
    ) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        failures: list[str] = []

        def record(name: str, passed: bool, detail: Any) -> None:
            checks[name] = {"ok": bool(passed), "detail": detail}
            if not passed:
                failures.append(name)

        record("database", bool(base_api.DATABASE.ping()), "connected")
        encryption_ready = base_api.has_encryption_key(base_api.CONFIG.deriv.token_encryption_key)
        record(
            "token_encryption",
            encryption_ready,
            "configured" if encryption_ready else "missing",
        )
        control_key_ready = bool(os.getenv("CONTROL_API_KEY", "").strip())
        record(
            "control_api_key",
            control_key_ready,
            "configured" if control_key_ready else "missing",
        )
        record("oauth", True, copy.deepcopy(oauth_configuration))

        summary = base_api.REPOSITORY.summary()
        heartbeat_text = str(summary.get("last_heartbeat") or "")
        heartbeat_age: float | None = None
        if heartbeat_text:
            try:
                heartbeat = datetime.fromisoformat(heartbeat_text)
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                heartbeat_age = max(
                    0.0,
                    (datetime.now(timezone.utc) - heartbeat).total_seconds(),
                )
            except ValueError:
                heartbeat_age = None
        record(
            "worker_heartbeat",
            heartbeat_age is not None and heartbeat_age <= 45.0,
            {"last_heartbeat": heartbeat_text or None, "age_seconds": heartbeat_age},
        )

        dashboard_details: dict[str, Any] = {}
        for mode in ("demo", "real"):
            try:
                payload = verified_dashboard_summary(account_type=mode, force=True)
                performance = dict(payload.get("system_performance") or {})
                today = dict(performance.get("today") or {})
                total = int(today.get("total_trades") or 0)
                wins = int(today.get("wins") or 0)
                losses = int(today.get("losses") or 0)
                mode_ok = (
                    payload.get("dashboard_account_type") == mode
                    and total == wins + losses
                    and int(payload.get("snapshot_version") or 0) > 0
                    and bool(payload.get("generated_at"))
                )
                dashboard_details[mode] = {
                    "ok": mode_ok,
                    "snapshot_version": int(payload.get("snapshot_version") or 0),
                    "generated_at": payload.get("generated_at"),
                    "total_trades": total,
                    "wins": wins,
                    "losses": losses,
                }
                if not mode_ok:
                    failures.append(f"dashboard_{mode}")
            except Exception as exc:
                dashboard_details[mode] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
                failures.append(f"dashboard_{mode}")
        checks["dashboard_modes"] = {
            "ok": all(item.get("ok") for item in dashboard_details.values()),
            "detail": dashboard_details,
        }

        critical_route_counts: dict[str, int] = {}
        for path in (
            "/oauth/start",
            "/oauth/callback",
            "/ws/dashboard",
            "/metrics/summary",
            "/me",
        ):
            count = sum(1 for route in app.router.routes if getattr(route, "path", None) == path)
            critical_route_counts[path] = count
            if count != 1:
                failures.append(f"route:{path}")
        checks["critical_routes"] = {
            "ok": all(count == 1 for count in critical_route_counts.values()),
            "detail": critical_route_counts,
        }

        response = {
            "status": "ready" if not failures else "not_ready",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "model_version": base_api.CONFIG.model.version,
            "transport": "private_deriv_websocket",
            "checks": checks,
            "failures": failures,
        }
        if failures:
            raise HTTPException(status_code=503, detail=response)
        return response

    @app.on_event("startup")
    async def start_mode_aware_dashboard_publisher() -> None:
        global _RUNTIME_LOOP, _DASHBOARD_EVENT
        _RUNTIME_LOOP = asyncio.get_running_loop()
        _DASHBOARD_EVENT = asyncio.Event()
        _DASHBOARD_EVENT.set()

        async def publish_loop() -> None:
            while True:
                triggered = True
                try:
                    await asyncio.wait_for(_DASHBOARD_EVENT.wait(), timeout=20.0)
                except asyncio.TimeoutError:
                    triggered = False
                _DASHBOARD_EVENT.clear()
                if triggered:
                    await asyncio.sleep(0.45)
                for mode in ("demo", "real"):
                    if not await HUB.has_clients(mode):
                        continue
                    try:
                        data = await asyncio.to_thread(
                            verified_dashboard_summary,
                            account_type=mode,
                        )
                        await HUB.broadcast(
                            mode,
                            {"type": "snapshot", "mode": mode, "data": data},
                        )
                    except Exception as exc:
                        base_api.LOGGER.exception(
                            "MODE_AWARE_DASHBOARD_BROADCAST_FAILED mode=%s error=%s",
                            mode,
                            exc,
                        )

        asyncio.create_task(publish_loop(), name="mode-aware-dashboard-publisher")

    _INSTALLED = True
