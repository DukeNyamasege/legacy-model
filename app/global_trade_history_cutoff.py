from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import or_, select

import app.api as base_api
from app.final_personal_trade_stream import (
    _aidr_summary,
    _sort_time,
    _virtual_rows_with_progress,
)
from app.final_public_controls import (
    ClearTradesRequest,
    _current_account_payload,
    _remove_route,
    _reporting_timezone,
    _today_bounds_utc,
    _trade_to_payload,
)
from app.models import (
    AccountRiskState,
    CandidateSignalRecord,
    DirectionalSignal,
    ManagedAccount,
    RuntimePreference,
    Trade,
    VirtualTrade,
    utc_now,
)


_INSTALLED = False
_CUTOFF_PREFIX = "personal_trade_history_cutoff:v1:"


def _cutoff_key(managed_account_id: int) -> str:
    return f"{_CUTOFF_PREFIX}{int(managed_account_id)}"


def _parse_cutoff(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _read_cutoff(session: Any, managed_account_id: int) -> datetime | None:
    row = session.get(RuntimePreference, _cutoff_key(managed_account_id))
    return _parse_cutoff(row.preference_value if row is not None else None)


def _write_cutoff_value(
    session: Any,
    managed_account_id: int,
    value: datetime,
) -> datetime:
    normalized = value.astimezone(timezone.utc)
    key = _cutoff_key(managed_account_id)
    text = normalized.isoformat()
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=text))
    else:
        row.preference_value = text
        row.updated_at = utc_now()
    return normalized


def _write_cutoff(session: Any, managed_account_id: int) -> datetime:
    return _write_cutoff_value(
        session,
        managed_account_id,
        datetime.now(timezone.utc),
    )


def install_global_trade_history_cutoff(app: Any) -> None:
    """Make Reset/Clear the persistent account-wide visibility boundary.

    The first dashboard read seeds a durable boundary at the start of the current
    reporting day. That preserves the familiar current-day initial view without a
    midnight expiry: every subsequent manual or scheduled contract remains visible
    across logout/login, browsers, devices and future days until explicit Reset.
    Reset moves the boundary forward. Historical database rows are never deleted.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/trades/today", "GET")
    _remove_route(app, "/me/clear-trades", "POST")

    @app.post("/me/clear-trades")
    def global_clear_trades(
        request: Request,
        body: ClearTradesRequest,
    ) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        scope = str(body.scope or "all").strip().lower()
        if scope not in {"today", "all"}:
            raise HTTPException(status_code=400, detail="scope must be today or all")

        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id, with_for_update=True)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")
            cutoff = _write_cutoff(session, managed_id)
            enabled = bool(row.enabled)
            execution_status = str(row.execution_status or "inactive")

        base_api.REPOSITORY.audit(
            "GLOBAL_PERSONAL_TRADE_HISTORY_CLEARED",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {
                "managed_account_id": managed_id,
                "requested_scope": scope,
                "history_cleared_at": cutoff.isoformat(),
                "execution_preserved": True,
            },
        )
        return {
            "success": True,
            "scope": scope,
            "history_cleared_at": cutoff.isoformat(),
            "history_visibility": "from_cutoff_forward_until_next_reset",
            "global_across_sessions": True,
            "cross_day_until_next_reset": True,
            "execution_preserved": True,
            "enabled": enabled,
            "execution_status": execution_status,
            "message": "Trade view reset globally. New trades remain visible until the next Reset.",
        }

    @app.get("/me/trades/today")
    def global_personal_trade_stream(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        today_start, _today_end = _today_bounds_utc()

        with base_api.DATABASE.session() as session:
            cutoff = _read_cutoff(session, managed_id)
            if cutoff is None:
                # Safe one-time migration: show today's existing trades, then keep
                # that boundary forever until the trader explicitly presses Reset.
                # This prevents a schedule created today from disappearing at
                # midnight without exposing years of pre-upgrade history.
                cutoff = _write_cutoff_value(session, managed_id, today_start)

            actual_query = (
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
                .where(
                    or_(
                        Trade.purchase_time >= cutoff,
                        Trade.provider_purchase_time >= cutoff,
                    )
                )
            )
            virtual_query = (
                select(VirtualTrade)
                .where(VirtualTrade.managed_account_id == managed_id)
                .where(VirtualTrade.created_at >= cutoff)
            )

            actual_rows = session.execute(
                actual_query.order_by(Trade.purchase_time.desc()).limit(5000)
            ).all()
            virtual_rows = session.scalars(
                virtual_query.order_by(VirtualTrade.created_at.asc()).limit(5000)
            ).all()
            state = session.get(AccountRiskState, managed_id)

        actual_trades = [
            {
                **_trade_to_payload(trade, candidate, directional),
                "is_virtual": False,
                "trade_kind": "actual",
                "history_retained": True,
            }
            for trade, candidate, directional in actual_rows
        ]
        virtual_trades = _virtual_rows_with_progress(
            list(virtual_rows),
            managed_account_id=managed_id,
        )
        trades = sorted(
            [*actual_trades, *virtual_trades],
            key=_sort_time,
            reverse=True,
        )

        wins = sum(str(row.get("outcome") or "").upper() == "WIN" for row in actual_trades)
        losses = sum(str(row.get("outcome") or "").upper() == "LOSS" for row in actual_trades)
        open_trades = sum(
            str(row.get("outcome") or "OPEN").upper() not in {"WIN", "LOSS"}
            for row in actual_trades
        )
        profit = sum(float(row.get("profit") or 0.0) for row in actual_trades)
        aidr = _aidr_summary(state, managed_id)
        cutoff_iso = cutoff.isoformat()

        return {
            "authenticated": True,
            "account": str(account.get("account_id_masked") or ""),
            "account_type": str(account.get("account_type") or "demo"),
            "timezone": str(_reporting_timezone()),
            "date": datetime.now(_reporting_timezone()).date().isoformat(),
            "session_started_at": cutoff_iso,
            "history_cleared_at": cutoff_iso,
            "history_visibility": "from_cutoff_forward_until_next_reset",
            "history_visibility_global": True,
            "history_preserved_across_stop": True,
            "history_preserved_across_midnight": True,
            "trades": trades,
            "aidr": aidr,
            "summary": {
                "total": len(actual_trades),
                "settled": wins + losses,
                "wins": wins,
                "losses": losses,
                "open": open_trades,
                "profit": round(profit, 8),
                "win_rate": wins / (wins + losses) if wins + losses else 0.0,
                "virtual_observations": len(virtual_trades),
                "virtual_wins": int(aidr["virtual_wins"]),
                "virtual_wins_required": int(aidr["virtual_wins_required"]),
                "virtual_losses": int(aidr["virtual_losses"]),
                "virtual_open": sum(row.get("outcome") == "OPEN" for row in virtual_trades),
                "history_rows": len(trades),
            },
        }

    app.state.global_trade_history_cutoff_installed = True
    _INSTALLED = True
