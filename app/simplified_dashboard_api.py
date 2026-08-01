from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Request

import app.api as base_api

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


def install_simplified_dashboard_api() -> None:
    """Expose lightweight personal-dashboard data for the standalone UI.

    This deliberately reuses the canonical repository and current browser session.
    It does not create a second account model or change execution state.
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
        trades: list[dict[str, Any]] = []
        for row in rows:
            timestamp = _parse_timestamp(
                row.get("purchase_time")
                or row.get("provider_purchase_time")
                or row.get("settlement_time")
            )
            if timestamp is None or timestamp.astimezone(timezone_value).date() != today:
                continue
            trades.append(row)

        wins = sum(str(row.get("outcome") or "").upper() == "WIN" for row in trades)
        losses = sum(str(row.get("outcome") or "").upper() == "LOSS" for row in trades)
        open_trades = sum(
            str(row.get("outcome") or "OPEN").upper() not in {"WIN", "LOSS"}
            for row in trades
        )
        profit = sum(float(row.get("profit") or 0.0) for row in trades)

        return {
            "authenticated": True,
            "account": str(account.get("account_id_masked") or ""),
            "account_type": str(account.get("account_type") or "demo"),
            "timezone": str(timezone_value),
            "date": today.isoformat(),
            "trades": trades,
            "summary": {
                "total": len(trades),
                "settled": wins + losses,
                "wins": wins,
                "losses": losses,
                "open": open_trades,
                "profit": round(profit, 8),
                "win_rate": wins / (wins + losses) if wins + losses else 0.0,
            },
        }

    base_api.app.state.simplified_dashboard_api_installed = True
    _INSTALLED = True
