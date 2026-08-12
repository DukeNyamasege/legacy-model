from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import case, func, or_, select

import app.api as base_api
from app.dashboard_live_events import _runtime_state
from app.final_personal_trade_stream import _sort_time, _virtual_rows_with_progress
from app.final_public_controls import (
    _current_account_payload,
    _remove_route,
    _reporting_timezone,
    _today_bounds_utc,
    _trade_to_payload,
)
from app.models import CandidateSignalRecord, DirectionalSignal, ManagedAccount, Trade, VirtualTrade


_INSTALLED = False
_RECENT_LIMIT = 100


def _actual_day_filter(start: datetime, end: datetime):
    return or_(
        Trade.purchase_time.between(start, end),
        Trade.settlement_time.between(start, end),
        Trade.provider_purchase_time.between(start, end),
    )


def _virtual_day_filter(start: datetime, end: datetime):
    return or_(
        VirtualTrade.created_at.between(start, end),
        VirtualTrade.settled_at.between(start, end),
    )


def _runtime_lifecycle(enabled: bool, status: str) -> str:
    normalized = str(status or "inactive").strip().lower()
    if enabled:
        return "running"
    if normalized in {"manual_pause", "take_profit", "stop_loss", "reconnecting"}:
        return "paused"
    return "stopped"


def _live_snapshot(request: Request) -> dict[str, Any]:
    """One bounded account read for the current mobile dashboard.

    The old dashboard requested /me, lifecycle and a 5,000-row trade history in
    parallel on every refresh. This endpoint keeps runtime state and recent rows in
    one database session while aggregate KPIs stay accurate for the whole Nairobi
    trading day.
    """

    account = _current_account_payload(request)
    managed_id = int(account["id"])
    start, end = _today_bounds_utc()

    with base_api.DATABASE.session() as session:
        row = session.get(ManagedAccount, managed_id)
        if row is None:
            return {"authenticated": False, "lifecycle": "missing"}

        totals = session.execute(
            select(
                func.count(Trade.id),
                func.coalesce(
                    func.sum(case((func.upper(Trade.outcome) == "WIN", 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((func.upper(Trade.outcome) == "LOSS", 1), else_=0)),
                    0,
                ),
                func.coalesce(func.sum(Trade.profit), 0.0),
            )
            .where(Trade.managed_account_id == managed_id)
            .where(_actual_day_filter(start, end))
        ).one()

        recent_actual = session.execute(
            select(Trade, CandidateSignalRecord, DirectionalSignal)
            .outerjoin(
                CandidateSignalRecord,
                CandidateSignalRecord.signal_id == Trade.signal_id,
            )
            .outerjoin(
                DirectionalSignal,
                DirectionalSignal.signal_id == Trade.signal_id,
            )
            .where(Trade.managed_account_id == managed_id)
            .where(_actual_day_filter(start, end))
            .order_by(Trade.purchase_time.desc())
            .limit(_RECENT_LIMIT)
        ).all()

        recent_virtual = session.scalars(
            select(VirtualTrade)
            .where(VirtualTrade.managed_account_id == managed_id)
            .where(_virtual_day_filter(start, end))
            .order_by(VirtualTrade.created_at.desc())
            .limit(_RECENT_LIMIT)
        ).all()
        virtual_total = int(
            session.scalar(
                select(func.count(VirtualTrade.id))
                .where(VirtualTrade.managed_account_id == managed_id)
                .where(_virtual_day_filter(start, end))
            )
            or 0
        )

        enabled = bool(row.enabled)
        status = str(row.execution_status or "inactive").strip().lower()
        reason = str(row.execution_status_reason or "")
        updated_at = row.execution_status_updated_at or row.updated_at

    actual_rows = [
        {
            **_trade_to_payload(trade, candidate, directional),
            "is_virtual": False,
            "trade_kind": "actual",
            "history_retained": True,
        }
        for trade, candidate, directional in recent_actual
    ]
    # The formatter expects chronological input when calculating a displayed
    # virtual sequence. Reverse this bounded window before formatting, then merge
    # the result back into newest-first order.
    virtual_rows = _virtual_rows_with_progress(list(reversed(recent_virtual)))
    trades = sorted(
        [*actual_rows, *virtual_rows],
        key=_sort_time,
        reverse=True,
    )[:_RECENT_LIMIT]

    total = int(totals[0] or 0)
    wins = int(totals[1] or 0)
    losses = int(totals[2] or 0)
    profit = float(totals[3] or 0.0)
    runtime_state = _runtime_state(enabled=enabled, status=status)

    return {
        "authenticated": True,
        "managed_account_id": managed_id,
        "account": str(account.get("account_id_masked") or ""),
        "account_type": str(account.get("account_type") or "demo"),
        "timezone": str(_reporting_timezone()),
        "date": start.astimezone(_reporting_timezone()).date().isoformat(),
        "enabled": enabled,
        "runtime_state": runtime_state,
        "execution_status": status,
        "reason": reason,
        "lifecycle": _runtime_lifecycle(enabled, status),
        "updated_at": (
            updated_at.astimezone(timezone.utc).isoformat()
            if updated_at is not None
            else ""
        ),
        "trades": trades,
        "summary": {
            "total": total,
            "settled": wins + losses,
            "wins": wins,
            "losses": losses,
            "open": max(0, total - wins - losses),
            "profit": round(profit, 8),
            "win_rate": wins / (wins + losses) if wins + losses else 0.0,
            "virtual_observations": virtual_total,
            "history_rows": len(trades),
        },
        "transport": "bounded_account_snapshot",
    }


def _cached_summary(mode: str) -> dict[str, Any]:
    target = str(mode or "demo").strip().lower()
    if target not in {"demo", "real"}:
        target = "demo"
    with base_api.DASHBOARD_SUMMARY_LOCK:
        state = dict(base_api.DASHBOARD_SUMMARY_CACHE.get(target) or {})
        payload = copy.deepcopy(state.get("data")) if state.get("data") else None
        generated_at = state.get("generated_at")
        dirty = bool(state.get("dirty", True))
    if isinstance(payload, dict):
        payload["mode"] = target
        payload["cache_only"] = True
        payload["stale"] = dirty
        return payload
    return {
        "mode": target,
        "cache_only": True,
        "stale": True,
        "generated_at": generated_at,
        "performance_profile": "background-summary",
        "accounts": [],
        "totals": {},
    }


def install_seamless_dashboard_runtime(app: Any) -> None:
    """Install the final non-blocking personal dashboard data authority."""

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/live-snapshot", "GET")
    _remove_route(app, "/metrics/summary", "GET")

    @app.get("/me/live-snapshot", include_in_schema=False)
    def seamless_live_snapshot(request: Request) -> dict[str, Any]:
        return _live_snapshot(request)

    @app.get("/metrics/summary", include_in_schema=False)
    def seamless_cached_summary(mode: str = "demo") -> dict[str, Any]:
        # The Builder never needs a synchronous global replay. Old cached tabs can
        # still read the last completed summary, but no browser request is allowed
        # to start an 80-second accounting query on the API request thread.
        return _cached_summary(mode)

    app.state.seamless_dashboard_runtime_installed = True
    app.state.seamless_dashboard_runtime_version = "20260812-seamless-snapshot-v1"
    _INSTALLED = True
