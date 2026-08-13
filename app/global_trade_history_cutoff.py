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


def _write_cutoff(session: Any, managed_account_id: int) -> datetime:
    now = datetime.now(timezone.utc)
    key = _cutoff_key(managed_account_id)
    value = now.isoformat()
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=value))
    else:
        row.preference_value = value
        row.updated_at = utc_now()
    return now


def install_global_trade_history_cutoff(app: Any) -> None:
    """Make Clear Trades a persistent account-wide visibility boundary.

    Clearing history must survive logout/login, another browser and another device.
    Historical database rows remain available for audit/settlement integrity; the
    personal dashboard simply never returns rows whose trade was opened before the
    latest account cutoff. Clearing history never stops or resets trading.
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
            "history_visibility": "from_cutoff_forward",
            "global_across_sessions": True,
            "execution_preserved": True,
            "enabled": enabled,
            "execution_status": execution_status,
            "message": "Trade view cleared globally. Only trades opened after this point will appear.",
        }

    @app.get("/me/trades/today")
    def global_personal_trade_stream(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        start, end = _today_bounds_utc()

        with base_api.DATABASE.session() as session:
            cutoff = _read_cutoff(session, managed_id)

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
                        Trade.purchase_time.between(start, end),
                        Trade.settlement_time.between(start, end),
                        Trade.provider_purchase_time.between(start, end),
                    )
                )
            )
            virtual_query = (
                select(VirtualTrade)
                .where(VirtualTrade.managed_account_id == managed_id)
                .where(
                    or_(
                        VirtualTrade.created_at.between(start, end),
                        VirtualTrade.settled_at.between(start, end),
                    )
                )
            )

            if cutoff is not None:
                # Visibility is anchored to when the trade/observation opened. A
                # contract opened before Clear Trades must not reappear merely
                # because it settles after the cutoff.
                actual_query = actual_query.where(
                    or_(
                        Trade.purchase_time >= cutoff,
                        Trade.provider_purchase_time >= cutoff,
                    )
                )
                virtual_query = virtual_query.where(VirtualTrade.created_at >= cutoff)

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
        cutoff_iso = cutoff.isoformat() if cutoff is not None else None

        return {
            "authenticated": True,
            "account": str(account.get("account_id_masked") or ""),
            "account_type": str(account.get("account_type") or "demo"),
            "timezone": str(_reporting_timezone()),
            "date": start.astimezone(_reporting_timezone()).date().isoformat(),
            "session_started_at": cutoff_iso,
            "history_cleared_at": cutoff_iso,
            "history_visibility": "from_cutoff_forward",
            "history_visibility_global": True,
            "history_preserved_across_stop": True,
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
