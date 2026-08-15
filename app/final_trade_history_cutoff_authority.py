from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import Request
from sqlalchemy import case, func, or_, select

import app.api_performance_hardening as performance
import app.netlify_realtime_gateway as gateway
from app.final_public_controls import ClearTradesRequest, _today_bounds_utc
from app.global_trade_history_cutoff import _read_cutoff
from app.models import Trade, VirtualTrade


_INSTALLED = False
_ORIGINAL_FAST_TRADE_PAYLOAD: Callable[..., dict[str, Any]] | None = None
_ORIGINAL_ME_PAYLOAD: Callable[..., dict[str, Any]] | None = None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value).astimezone(timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed).astimezone(timezone.utc)


def _row_visible_after_cutoff(row: dict[str, Any], cutoff: datetime) -> bool:
    """Use the trade/observation OPEN time, never its later settlement time."""

    virtual = bool(row.get("is_virtual")) or str(row.get("trade_kind") or "").lower() == "virtual"
    if virtual:
        opened = _parse_time(row.get("created_at") or row.get("purchase_time"))
    else:
        opened = _parse_time(row.get("provider_purchase_time") or row.get("purchase_time"))
    return bool(opened is not None and opened >= cutoff)


def _cutoff_aggregate(managed_account_id: int) -> tuple[datetime, dict[str, Any]] | None:
    """Return unbounded post-clear financial KPIs and virtual observation counts."""

    managed_id = int(managed_account_id)
    start, end = _today_bounds_utc()
    actual_period = or_(
        Trade.purchase_time.between(start, end),
        Trade.settlement_time.between(start, end),
        Trade.provider_purchase_time.between(start, end),
    )
    virtual_period = or_(
        VirtualTrade.created_at.between(start, end),
        VirtualTrade.settled_at.between(start, end),
    )

    with performance.base_api.DATABASE.session() as session:
        cutoff = _read_cutoff(session, managed_id)
        if cutoff is None:
            return None

        # A contract opened before Clear Trades never re-enters the visible
        # session merely because it settles after the cutoff.
        actual_opened_after_cutoff = or_(
            Trade.purchase_time >= cutoff,
            Trade.provider_purchase_time >= cutoff,
        )
        actual = session.execute(
            select(
                func.count(Trade.id).label("total"),
                func.sum(case((Trade.outcome == "WIN", 1), else_=0)).label("wins"),
                func.sum(case((Trade.outcome == "LOSS", 1), else_=0)).label("losses"),
                func.sum(Trade.profit).label("profit"),
                func.sum(
                    case((Trade.settlement_time.is_(None), 1), else_=0)
                ).label("open_trades"),
            ).where(
                Trade.managed_account_id == managed_id,
                actual_period,
                actual_opened_after_cutoff,
            )
        ).one()

        virtual = session.execute(
            select(
                func.count(VirtualTrade.id).label("total"),
                func.sum(
                    case((VirtualTrade.result.ilike("%WIN%"), 1), else_=0)
                ).label("wins"),
                func.sum(
                    case((VirtualTrade.result.ilike("%LOSS%"), 1), else_=0)
                ).label("losses"),
                func.sum(
                    case((VirtualTrade.result == "OPEN", 1), else_=0)
                ).label("open_trades"),
            ).where(
                VirtualTrade.managed_account_id == managed_id,
                virtual_period,
                VirtualTrade.created_at >= cutoff,
            )
        ).one()

    wins = int(actual.wins or 0)
    losses = int(actual.losses or 0)
    total = int(actual.total or 0)
    virtual_total = int(virtual.total or 0)
    return cutoff, {
        "total": total,
        "settled": wins + losses,
        "wins": wins,
        "losses": losses,
        "open": int(actual.open_trades or 0),
        "profit": round(float(actual.profit or 0.0), 8),
        "win_rate": wins / (wins + losses) if wins + losses else 0.0,
        "virtual_observations": virtual_total,
        "virtual_wins": int(virtual.wins or 0),
        "virtual_losses": int(virtual.losses or 0),
        "virtual_open": int(virtual.open_trades or 0),
        "history_rows": total + virtual_total,
    }


def _route_endpoint(app: Any, path: str, method: str) -> Callable[..., Any] | None:
    expected = method.upper()
    for route in reversed(list(app.router.routes)):
        if (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        ):
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


def install_final_trade_history_cutoff_authority(app: Any) -> None:
    """Keep Clear Trades global while allowing unlimited KPI totals afterward.

    Realtime deliberately transports only a bounded set of recent rows, but Runs,
    Wins, Losses and P/L must never be derived from that row window. They come from
    an unbounded PostgreSQL aggregate over the account's visible post-clear session.
    """

    global _INSTALLED, _ORIGINAL_FAST_TRADE_PAYLOAD, _ORIGINAL_ME_PAYLOAD
    if _INSTALLED:
        return

    _ORIGINAL_FAST_TRADE_PAYLOAD = performance._fast_trade_payload
    _ORIGINAL_ME_PAYLOAD = performance._me_payload

    def cutoff_fast_trade_payload(account: dict[str, Any], limit: int) -> dict[str, Any]:
        original = _ORIGINAL_FAST_TRADE_PAYLOAD
        if original is None:
            raise RuntimeError("Personal trade payload authority is unavailable")
        payload = dict(original(account, limit) or {})
        snapshot = _cutoff_aggregate(int(account["id"]))
        if snapshot is None:
            return payload

        cutoff, summary = snapshot
        cutoff_iso = cutoff.isoformat()
        rows = [
            dict(row)
            for row in list(payload.get("trades") or [])
            if isinstance(row, dict) and _row_visible_after_cutoff(row, cutoff)
        ]
        payload["trades"] = rows
        payload["summary"] = {
            **dict(payload.get("summary") or {}),
            **summary,
            "returned_rows": len(rows),
        }
        payload["session_started_at"] = cutoff_iso
        payload["history_cleared_at"] = cutoff_iso
        payload["history_visibility"] = "from_cutoff_forward"
        payload["history_visibility_global"] = True
        payload["truncated"] = int(summary["history_rows"]) > len(rows)
        payload["performance_profile"] = "bounded-rows-unbounded-cutoff-kpis-v2"
        return payload

    def cutoff_me_payload(account: dict[str, Any]) -> dict[str, Any]:
        original = _ORIGINAL_ME_PAYLOAD
        if original is None:
            raise RuntimeError("Personal account payload authority is unavailable")
        payload = dict(original(account) or {})
        snapshot = _cutoff_aggregate(int(account["id"]))
        if snapshot is None:
            return payload

        cutoff, summary = snapshot
        stats = dict(payload.get("stats") or {})
        stats.update(
            {
                "trades": int(summary["total"]),
                "settled_trades": int(summary["settled"]),
                "open_trades": int(summary["open"]),
                "wins": int(summary["wins"]),
                "losses": int(summary["losses"]),
                "profit": float(summary["profit"]),
            }
        )
        payload["stats"] = stats
        payload["history_cleared_at"] = cutoff.isoformat()
        payload["history_visibility_global"] = True
        return payload

    # The performance REST routes resolve these globals at request time.
    performance._fast_trade_payload = cutoff_fast_trade_payload
    performance._me_payload = cutoff_me_payload

    # netlify_realtime_gateway imported function aliases by value, so patch those
    # aliases too. Otherwise WebSocket snapshots could restore pre-clear totals.
    gateway._fast_trade_payload = cutoff_fast_trade_payload
    gateway._me_payload = cutoff_me_payload

    original_clear = _route_endpoint(app, "/me/clear-trades", "POST")
    if original_clear is None:
        raise RuntimeError("Clear Trades route must exist before final cutoff authority")
    _remove_route(app, "/me/clear-trades", "POST")

    @app.post("/me/clear-trades")
    async def clear_trades_and_invalidate_realtime(
        request: Request,
        body: ClearTradesRequest,
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(original_clear, request, body)
        performance._clear_response_caches()
        await gateway._HUB.publish()
        return {
            **dict(result or {}),
            "kpi_reset": True,
            "kpi_source": "unbounded_post_cutoff_database_aggregate",
        }

    app.state.final_trade_history_cutoff_authority_installed = True
    app.state.trade_kpi_policy = "unbounded_post_cutoff_database_aggregate"
    _INSTALLED = True
