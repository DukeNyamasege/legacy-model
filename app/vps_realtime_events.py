from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any

from fastapi import Depends, Request

import app.api as base_api
import app.netlify_realtime_gateway as realtime_gateway
from app.dashboard_stability_fix import _remove_route


_INSTALLED = False
_ORIGINAL_COMBINED_SNAPSHOT: Any = None
_ORIGINAL_LIVE_SNAPSHOT: Any = None
_EVENT_LOCK = threading.RLock()
_RUNTIME_EVENTS: dict[int, deque[dict[str, Any]]] = {}
_EVENT_REVISIONS: dict[int, int] = {}
_MAX_EVENTS_PER_ACCOUNT = 12
_ALLOWED_EVENT_TYPES = {
    "scanner_ready",
    "condition_not_met",
    "condition_met",
    "trade_preparing",
    "trade_open",
    "virtual_observation",
    "execution_cancelled",
}


def _safe_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _events_for(managed_id: int) -> list[dict[str, Any]]:
    with _EVENT_LOCK:
        rows = _RUNTIME_EVENTS.get(int(managed_id))
        return [dict(item) for item in list(rows or [])]


def _event_revision(managed_id: int) -> int:
    with _EVENT_LOCK:
        return int(_EVENT_REVISIONS.get(int(managed_id), 0) or 0)


def _store_event(raw: dict[str, Any]) -> bool:
    try:
        managed_id = int(raw.get("managed_account_id"))
    except (TypeError, ValueError):
        return False
    event_type = _safe_text(raw.get("event"), 48).lower()
    if event_type not in _ALLOWED_EVENT_TYPES:
        return False
    try:
        tick_sequence = max(0, int(raw.get("tick_sequence") or 0))
    except (TypeError, ValueError):
        tick_sequence = 0
    try:
        digit_value = raw.get("digit")
        digit = int(digit_value) if digit_value is not None else None
    except (TypeError, ValueError):
        digit = None
    if digit is not None and not 0 <= digit <= 9:
        digit = None
    try:
        emitted_at = float(raw.get("emitted_at") or time.time())
    except (TypeError, ValueError):
        emitted_at = time.time()

    event = {
        "event": event_type,
        "message": _safe_text(raw.get("message"), 180),
        "symbol": _safe_text(raw.get("symbol"), 32),
        "tick_sequence": tick_sequence,
        "digit": digit,
        "emitted_at": emitted_at,
    }
    with _EVENT_LOCK:
        rows = _RUNTIME_EVENTS.setdefault(
            managed_id,
            deque(maxlen=_MAX_EVENTS_PER_ACCOUNT),
        )
        newest = rows[0] if rows else None
        # The worker can evaluate several selected markets quickly. Avoid storing
        # exact duplicates while retaining every meaningful state transition.
        if newest and all(
            newest.get(key) == event.get(key)
            for key in ("event", "symbol", "tick_sequence", "digit", "message")
        ):
            return False
        rows.appendleft(event)
        _EVENT_REVISIONS[managed_id] = _EVENT_REVISIONS.get(managed_id, 0) + 1
    return True


def install_vps_realtime_events(app: Any) -> None:
    """Add a same-VPS, non-persistent strategy progress stream to live snapshots.

    Worker decisions are delivered over the private Docker network and kept only
    in API memory. No per-tick PostgreSQL writes are introduced. The existing
    signed browser WebSocket remains the sole public realtime transport.
    """

    global _INSTALLED, _ORIGINAL_COMBINED_SNAPSHOT, _ORIGINAL_LIVE_SNAPSHOT
    if _INSTALLED:
        return

    _ORIGINAL_COMBINED_SNAPSHOT = realtime_gateway._combined_snapshot
    _ORIGINAL_LIVE_SNAPSHOT = realtime_gateway._live_snapshot

    def live_snapshot_with_runtime_revision(managed_id: int) -> dict[str, Any]:
        original = _ORIGINAL_LIVE_SNAPSHOT
        payload = dict(original(managed_id) or {}) if original is not None else {}
        base_revision = str(payload.get("revision") or "")
        payload["revision"] = f"{base_revision}|vps-events:{_event_revision(managed_id)}"
        return payload

    def combined_snapshot_with_runtime_events(account: dict[str, Any]) -> dict[str, Any]:
        original = _ORIGINAL_COMBINED_SNAPSHOT
        payload = original(account) if original is not None else {}
        managed_id = int(account["id"])
        payload["runtime_events"] = _events_for(managed_id)
        payload["runtime_event_revision"] = _event_revision(managed_id)
        payload["frontend_runtime"] = "full_vps_same_origin"
        return payload

    # The realtime gateway suppresses duplicate snapshots by revision. Runtime
    # strategy events intentionally do not touch PostgreSQL, so add a per-account
    # in-memory revision to the same comparison. A worker event can now wake the
    # browser immediately without a fake trade/database write.
    realtime_gateway._live_snapshot = live_snapshot_with_runtime_revision
    realtime_gateway._combined_snapshot = combined_snapshot_with_runtime_events

    # Full-VPS mode still uses the lightweight signed realtime snapshot. Never
    # revive the historical global summary rebuild merely because Netlify is gone.
    def vps_mark_dashboard_dirty(_account_type: str | None = None) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(realtime_gateway._HUB.publish())

    base_api.mark_dashboard_dirty = vps_mark_dashboard_dirty
    app.state.legacy_dashboard_summary_disabled = True

    _remove_route(app, "/health/frontend-backend", "GET")

    @app.get("/health/frontend-backend", include_in_schema=False)
    def vps_frontend_backend_health() -> dict[str, Any]:
        return {
            "status": "ready",
            "frontend": "vps_nginx_static",
            "backend": "vps_api_worker_postgres",
            "rest_transport": "vps_same_origin_caddy",
            "realtime_transport": "vps_same_origin_signed_websocket",
            "browser_controls_worker_lifetime": False,
            "legacy_summary_disabled": True,
            "runtime_event_stream": "docker_private_memory_fanout",
        }

    @app.post("/control/internal/vps-runtime-events", include_in_schema=False)
    async def receive_vps_runtime_events(
        request: Request,
        _administrator: str = Depends(base_api.require_control_auth),
    ) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        raw_events = body.get("events") if isinstance(body, dict) else []
        if not isinstance(raw_events, list):
            raw_events = []
        accepted = 0
        for item in raw_events[:200]:
            if isinstance(item, dict) and _store_event(item):
                accepted += 1
        generation = realtime_gateway._HUB.generation
        if accepted:
            generation = await realtime_gateway._HUB.publish()
        return {
            "accepted": accepted,
            "realtime_generation": generation,
            "storage": "ephemeral_api_memory",
        }

    app.state.vps_realtime_events_installed = True
    app.state.frontend_architecture = "full-vps-same-origin-v2"
    _INSTALLED = True
