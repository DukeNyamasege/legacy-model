from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any, AsyncIterator

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

import app.api as base_api
from app.dashboard_stability_fix import _remove_route
from app.models import ManagedAccount, Trade, VirtualTrade


_INSTALLED = False

_FATAL_STATUSES = {
    "error",
    "credential_error",
    "invalid_account",
    "token_required",
    "purchase_registration_error",
    "contract_unavailable",
}
_STARTING_STATUSES = {"starting", "connecting", "validating", "reconnecting"}
_WAITING_STATUSES = {"waiting_for_condition", "ready", "watching"}
_EXECUTING_STATUSES = {"executing", "purchasing", "proposal"}
_RUNNING_STATUSES = {
    "running",
    "active",
    "virtual_protection",
    "recovery_pending",
    "base_stake_protection",
}


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _runtime_state(*, enabled: bool, status: str) -> str:
    normalized = str(status or "inactive").strip().lower()
    if normalized in _FATAL_STATUSES:
        return "ERROR"
    if normalized in _STARTING_STATUSES:
        return "STARTING"
    if normalized in _WAITING_STATUSES:
        return "WAITING_FOR_CONDITION"
    if normalized in _EXECUTING_STATUSES:
        return "EXECUTING"
    if normalized in _RUNNING_STATUSES and enabled:
        return "RUNNING"
    return "STOPPED"


def _live_snapshot(managed_id: int) -> dict[str, Any] | None:
    """Return a tiny revision snapshot without loading dashboard history.

    The browser holds one SSE connection and reacts only when this revision changes.
    This keeps the dashboard responsive without polling full personal history every
    few seconds. Closing the browser only disconnects this read-only stream; it does
    not mutate ManagedAccount.enabled and therefore cannot stop server-side trading.
    """

    with base_api.DATABASE.session() as session:
        row = session.get(ManagedAccount, int(managed_id))
        if row is None:
            return None

        latest_actual = session.execute(
            select(
                func.max(Trade.id),
                func.max(Trade.purchase_time),
                func.max(Trade.settlement_time),
            ).where(Trade.managed_account_id == int(managed_id))
        ).one()
        latest_virtual = session.execute(
            select(
                func.max(VirtualTrade.id),
                func.max(VirtualTrade.created_at),
                func.max(VirtualTrade.settled_at),
            ).where(VirtualTrade.managed_account_id == int(managed_id))
        ).one()

        enabled = bool(row.enabled)
        status = str(row.execution_status or "inactive").strip().lower()
        reason = str(row.execution_status_reason or "")
        status_updated_at = _iso(row.execution_status_updated_at)
        row_updated_at = _iso(row.updated_at)
        revision_parts = (
            int(latest_actual[0] or 0),
            _iso(latest_actual[1]),
            _iso(latest_actual[2]),
            int(latest_virtual[0] or 0),
            _iso(latest_virtual[1]),
            _iso(latest_virtual[2]),
            status_updated_at,
            row_updated_at,
            enabled,
            status,
        )

    return {
        "authenticated": True,
        "enabled": enabled,
        "runtime_state": _runtime_state(enabled=enabled, status=status),
        "execution_status": status,
        "reason": reason,
        "updated_at": status_updated_at,
        "revision": "|".join(str(item) for item in revision_parts),
    }


async def _event_stream(request: Request, managed_id: int) -> AsyncIterator[str]:
    last_revision = ""
    last_heartbeat = 0.0
    yield "retry: 1500\n\n"

    while True:
        if await request.is_disconnected():
            return

        snapshot = await asyncio.to_thread(_live_snapshot, int(managed_id))
        if snapshot is None:
            yield "event: account\ndata: {\"authenticated\":false}\n\n"
            return

        revision = str(snapshot.get("revision") or "")
        if revision != last_revision:
            last_revision = revision
            payload = json.dumps(snapshot, separators=(",", ":"))
            yield f"event: snapshot\ndata: {payload}\n\n"
            last_heartbeat = time.monotonic()
        elif time.monotonic() - last_heartbeat >= 15.0:
            yield ": heartbeat\n\n"
            last_heartbeat = time.monotonic()

        await asyncio.sleep(0.75)


def install_dashboard_live_events(app: Any) -> None:
    """Install read-only SSE events for personal runtime/trade revisions."""

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/live-events", "GET")

    @app.get("/me/live-events", include_in_schema=False)
    async def dashboard_live_events(request: Request) -> StreamingResponse:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        managed_id = int(account["id"])
        return StreamingResponse(
            _event_stream(request, managed_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-FOA-Live-Dashboard": "sse-v1",
            },
        )

    app.state.dashboard_live_events_installed = True
    app.state.dashboard_live_events_version = "20260812-sse-v1"
    _INSTALLED = True
