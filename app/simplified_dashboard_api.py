from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select

import app.api as base_api
from app.models import VirtualTrade

_INSTALLED = False


def _reporting_timezone() -> ZoneInfo:
    name = (
        os.getenv("TRADING_REPORT_TIMEZONE")
        or os.getenv("DASHBOARD_TIMEZONE")
        or "Africa/Nairobi"
    ).strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def _today_bounds_utc(timezone_value: ZoneInfo) -> tuple[datetime, datetime]:
    local_now = datetime.now(timezone.utc).astimezone(timezone_value)
    local_start = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=timezone_value)
    local_end = datetime.combine(local_now.date(), datetime.max.time(), tzinfo=timezone_value)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _virtual_outcome(result: Any) -> str:
    value = str(result or "OPEN").strip().upper()
    if "WIN" in value:
        return "WIN"
    if "LOSS" in value:
        return "LOSS"
    return "OPEN"


def _virtual_history_row(row: VirtualTrade) -> dict[str, Any]:
    barrier = str(row.barrier or "3").strip()
    outcome = _virtual_outcome(row.result)
    return {
        "id": f"virtual-{int(row.id)}",
        "trade_id": str(row.virtual_trade_id or f"virtual-{int(row.id)}"),
        "virtual_trade_id": str(row.virtual_trade_id or ""),
        "is_virtual": True,
        "trade_kind": "virtual",
        "symbol": str(row.market or ""),
        "market": str(row.market or ""),
        # The existing recent-trades renderer displays contract_type as its badge.
        # Keep the row visually identical to real trades while making the $0 mode
        # impossible to mistake for a purchased contract.
        "contract_type": f"VIRTUAL OVER {barrier}",
        "type": "VIRTUAL TRADE",
        "barrier": barrier,
        "buy_price": float(row.simulated_stake or 0.0),
        "stake": float(row.simulated_stake or 0.0),
        "simulated_stake": float(row.simulated_stake or 0.0),
        "payout": float(row.expected_payout) if row.expected_payout is not None else None,
        "expected_payout": (
            float(row.expected_payout) if row.expected_payout is not None else None
        ),
        # Virtual observations never affect actual account statistics or money.
        "profit": 0.0,
        "actual_profit_loss": 0.0,
        "amount_charged": 0.0,
        "outcome": outcome,
        "virtual_result": str(row.result or "OPEN"),
        "display_result": f"VIRTUAL {outcome}",
        "exit_digit": row.actual_last_digit,
        "actual_last_digit": row.actual_last_digit,
        "exit_spot": row.exit_spot,
        "purchase_time": row.created_at.isoformat() if row.created_at else None,
        "provider_purchase_time": row.created_at.isoformat() if row.created_at else None,
        "settlement_time": row.settled_at.isoformat() if row.settled_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "settled_at": row.settled_at.isoformat() if row.settled_at else None,
        "financial_impact_label": "$0.00",
    }


def _sort_timestamp(row: dict[str, Any]) -> datetime:
    return _parse_timestamp(
        row.get("purchase_time")
        or row.get("provider_purchase_time")
        or row.get("created_at")
        or row.get("settlement_time")
    ) or datetime.min.replace(tzinfo=timezone.utc)


def install_simplified_dashboard_api() -> None:
    """Expose lightweight personal-dashboard data for the standalone UI.

    Actual contracts and $0 virtual protection observations are returned in one
    chronological trade stream. Summary statistics remain actual-money-only, so
    virtual wins/losses cannot inflate the trader's financial win rate or P/L.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    @base_api.app.get("/me/trades/today")
    def personal_trades_today(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")

        timezone_value = _reporting_timezone()
        today = datetime.now(timezone.utc).astimezone(timezone_value).date()
        rows = base_api.REPOSITORY.recent_trades(
            5000,
            account_id=str(account["account_id"]),
        )
        actual_trades: list[dict[str, Any]] = []
        for row in rows:
            timestamp = _parse_timestamp(
                row.get("purchase_time")
                or row.get("provider_purchase_time")
                or row.get("settlement_time")
            )
            if timestamp is None or timestamp.astimezone(timezone_value).date() != today:
                continue
            item = dict(row)
            item.setdefault("is_virtual", False)
            item.setdefault("trade_kind", "actual")
            actual_trades.append(item)

        start_utc, end_utc = _today_bounds_utc(timezone_value)
        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            virtual_rows = session.scalars(
                select(VirtualTrade)
                .where(VirtualTrade.managed_account_id == managed_id)
                .where(
                    or_(
                        VirtualTrade.created_at.between(start_utc, end_utc),
                        VirtualTrade.settled_at.between(start_utc, end_utc),
                    )
                )
                .order_by(VirtualTrade.created_at.desc())
                .limit(5000)
            ).all()

        virtual_trades = [_virtual_history_row(row) for row in virtual_rows]
        trades = sorted(
            [*actual_trades, *virtual_trades],
            key=_sort_timestamp,
            reverse=True,
        )

        # Personal financial KPIs deliberately use actual contracts only.
        wins = sum(
            str(row.get("outcome") or "").upper() == "WIN" for row in actual_trades
        )
        losses = sum(
            str(row.get("outcome") or "").upper() == "LOSS" for row in actual_trades
        )
        open_trades = sum(
            str(row.get("outcome") or "OPEN").upper() not in {"WIN", "LOSS"}
            for row in actual_trades
        )
        profit = sum(float(row.get("profit") or 0.0) for row in actual_trades)
        virtual_wins = sum(row.get("outcome") == "WIN" for row in virtual_trades)
        virtual_losses = sum(row.get("outcome") == "LOSS" for row in virtual_trades)
        virtual_open = sum(row.get("outcome") == "OPEN" for row in virtual_trades)

        return {
            "authenticated": True,
            "account": str(account.get("account_id_masked") or ""),
            "account_type": str(account.get("account_type") or "demo"),
            "timezone": str(timezone_value),
            "date": today.isoformat(),
            "trades": trades,
            "summary": {
                "total": len(actual_trades),
                "settled": wins + losses,
                "wins": wins,
                "losses": losses,
                "open": open_trades,
                "profit": round(profit, 8),
                "win_rate": wins / (wins + losses) if wins + losses else 0.0,
                "virtual_observations": len(virtual_trades),
                "virtual_wins": virtual_wins,
                "virtual_losses": virtual_losses,
                "virtual_open": virtual_open,
                "history_rows": len(trades),
            },
        }

    @base_api.app.post("/me/logout")
    def standalone_logout() -> JSONResponse:
        response = JSONResponse({"success": True, "authenticated": False})
        response.delete_cookie(
            key=base_api.CLIENT_SESSION_COOKIE,
            path="/",
        )
        try:
            configured_domain = str(base_api.session_cookie_domain() or "").strip()
        except Exception:
            configured_domain = ""
        if configured_domain:
            response.delete_cookie(
                key=base_api.CLIENT_SESSION_COOKIE,
                path="/",
                domain=configured_domain,
            )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    base_api.app.state.simplified_dashboard_api_installed = True
    _INSTALLED = True