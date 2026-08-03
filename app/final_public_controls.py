from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, or_, select

import app.api as base_api
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

STOPPED_STATUSES = {"stopped", "inactive", "disabled", "real_disabled"}
PAUSED_STATUSES = {
    "manual_pause",
    "take_profit",
    "stop_loss",
    "insufficient_balance",
    "purchase_insufficient_balance",
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "contract_unavailable",
    "purchase_registration_error",
}
RUNNING_STATUSES = {
    "validating",
    "connecting",
    "active",
    "running",
    "reconnecting",
    "base_stake_protection",
    "recovery_pending",
    "virtual_protection",
}


class ClearTradesRequest(BaseModel):
    scope: str = "today"


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


def _reporting_timezone() -> ZoneInfo:
    return ZoneInfo("Africa/Nairobi")


def _today_bounds_utc() -> tuple[datetime, datetime]:
    zone = _reporting_timezone()
    today = datetime.now(timezone.utc).astimezone(zone).date()
    start = datetime.combine(today, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(today, time.max, tzinfo=zone).astimezone(timezone.utc)
    return start, end


def _current_account_payload(request: Request) -> dict[str, Any]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return account


def _load_managed_account(session, request: Request, *, for_update: bool = False) -> ManagedAccount:
    account = _current_account_payload(request)
    row = session.get(ManagedAccount, int(account["id"]), with_for_update=for_update)
    if row is None:
        raise HTTPException(status_code=401, detail="Managed account was not found")
    return row


def _reset_risk_state(session, managed_account_id: int) -> None:
    state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
    if state is None:
        return
    state.trading_day = ""
    state.daily_start_balance = 0.0
    state.session_profit = 0.0
    state.consecutive_losses = 0
    state.recovery_loss_debt = 0.0
    state.recovery_pending = False
    state.recovery_attempt_active = False
    state.protection_mode = "NORMAL_MODE"
    state.virtual_observation_count = 0
    state.virtual_win_count = 0
    state.virtual_loss_count = 0
    state.current_virtual_loss_streak = 0
    state.entered_virtual_mode_at = None
    state.recovery_pending_since = None
    state.equity_high_water = 0.0
    state.updated_at = utc_now()


def _clear_account_runtime_preferences(session, managed_account_id: int) -> int:
    prefixes = (
        f"aidr_split_remaining:{managed_account_id}",
        f"aidr_adaptive_trap:{managed_account_id}",
        f"aidr_over1_over3_v1:account_epoch:{managed_account_id}",
        f"hybrid_over2_put_v4:account_epoch:{managed_account_id}",
        f"hybrid_o2u7_put_v1:account_epoch:{managed_account_id}",
    )
    removed = 0
    for preference in session.scalars(select(RuntimePreference)).all():
        key = str(preference.preference_key or "")
        if any(key.startswith(prefix) for prefix in prefixes):
            session.delete(preference)
            removed += 1
    return removed


def _set_stopped(session, row: ManagedAccount) -> None:
    row.enabled = False
    row.execution_status = "stopped"
    row.execution_status_reason = (
        "Auto trading stopped completely. Next Start begins fresh from base stake."
    )[:160]
    row.execution_status_updated_at = utc_now()
    row.updated_at = utc_now()
    _reset_risk_state(session, int(row.id))
    _clear_account_runtime_preferences(session, int(row.id))


def _set_paused(session, row: ManagedAccount) -> None:
    row.enabled = False
    row.execution_status = "manual_pause"
    row.execution_status_reason = (
        "Auto trading paused. Recovery/session state is preserved for Resume."
    )[:160]
    row.execution_status_updated_at = utc_now()
    row.updated_at = utc_now()


def _set_started(session, row: ManagedAccount, *, reset_recovery: bool) -> None:
    if reset_recovery:
        _reset_risk_state(session, int(row.id))
        _clear_account_runtime_preferences(session, int(row.id))
    row.enabled = True
    row.execution_status = "connecting"
    row.execution_status_reason = (
        "Auto trading started manually. Worker will validate this account."
    )[:160]
    row.execution_status_updated_at = utc_now()
    row.updated_at = utc_now()


def _lifecycle_from_row(row: ManagedAccount) -> str:
    status = str(row.execution_status or "inactive").strip().lower()
    enabled = bool(row.enabled)
    if status in STOPPED_STATUSES:
        return "stopped"
    if not enabled:
        if status in RUNNING_STATUSES:
            # Defensive correction: disabled accounts must never be reported as
            # running merely because a background refresh wrote a runnable status.
            return "stopped" if status in {"inactive", "disabled"} else "paused"
        return "paused"
    if status in PAUSED_STATUSES:
        return "paused"
    return "running"


def _timestamp(value: Any) -> datetime | None:
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


def _trade_to_payload(
    trade: Trade,
    candidate: CandidateSignalRecord | None,
    directional: DirectionalSignal | None,
) -> dict[str, Any]:
    symbol = ""
    contract_type = ""
    barrier = ""
    if candidate is not None:
        symbol = candidate.symbol or symbol
        contract_type = candidate.contract_type or contract_type
        barrier = candidate.barrier or barrier
    if directional is not None:
        symbol = directional.symbol or symbol
        contract_type = directional.contract_type or contract_type
    return {
        "id": trade.id,
        "trade_id": trade.trade_id,
        "contract_id": trade.contract_id,
        "signal_id": trade.signal_id,
        "symbol": symbol,
        "market": symbol,
        "contract_type": contract_type,
        "barrier": barrier,
        "purchase_time": trade.purchase_time.isoformat() if trade.purchase_time else None,
        "settlement_time": trade.settlement_time.isoformat() if trade.settlement_time else None,
        "provider_purchase_time": trade.provider_purchase_time.isoformat() if trade.provider_purchase_time else None,
        "provider_settlement_time": trade.provider_settlement_time.isoformat() if trade.provider_settlement_time else None,
        "entry_tick": trade.entry_tick,
        "exit_tick": trade.exit_tick,
        "exit_spot": trade.exit_tick,
        "exit_digit": trade.exit_digit,
        "buy_price": trade.buy_price,
        "stake": trade.buy_price,
        "payout": trade.payout,
        "profit": trade.profit,
        "outcome": trade.outcome,
    }


def install_final_public_controls(app: Any) -> None:
    """Install final account lifecycle and personal reset controls.

    This module is intentionally installed last. It turns Stop/Pause/Resume into
    authoritative account lifecycle operations and exposes account-scoped trade
    reset APIs for the standalone public dashboard.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    for path, method in (
        ("/me/auto-trade", "POST"),
        ("/me/resume-trading", "POST"),
        ("/me/pause-trading", "POST"),
        ("/me/stop-trading", "POST"),
        ("/me/trading-lifecycle", "GET"),
        ("/me/trades/today", "GET"),
        ("/me/clear-trades", "POST"),
    ):
        _remove_route(app, path, method)

    @app.post("/me/stop-trading")
    def final_stop_trading(request: Request) -> dict[str, Any]:
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            _set_stopped(session, row)
            managed_account_id = int(row.id)
        base_api.REPOSITORY.audit(
            "FINAL_PERSONAL_TRADING_STOPPED",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {
                "managed_account_id": managed_account_id,
                "recovery_state_reset": True,
                "next_start_uses_base_stake": True,
            },
        )
        return {
            "success": True,
            "state": "stopped",
            "lifecycle": "stopped",
            "enabled": False,
            "message": "Auto trading stopped completely. Next Start begins fresh.",
        }

    @app.post("/me/pause-trading")
    def final_pause_trading(request: Request) -> dict[str, Any]:
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            _set_paused(session, row)
            managed_account_id = int(row.id)
        base_api.REPOSITORY.audit(
            "FINAL_PERSONAL_TRADING_PAUSED",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {"managed_account_id": managed_account_id, "recovery_state_preserved": True},
        )
        return {
            "success": True,
            "state": "paused",
            "lifecycle": "paused",
            "enabled": False,
            "message": "Auto trading paused. Resume continues the preserved state.",
        }

    @app.post("/me/resume-trading")
    def final_resume_trading(request: Request, body: base_api.ResumeTradeRequest) -> dict[str, Any]:
        account = _current_account_payload(request)
        if not account.get("has_trading_api_token", False):
            raise HTTPException(
                status_code=409,
                detail="Save a Deriv API token before starting auto trading.",
            )
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            previous = str(row.execution_status or "inactive").strip().lower()
            reset_recovery = body.mode == "start_again" or previous in STOPPED_STATUSES
            _set_started(session, row, reset_recovery=reset_recovery)
            managed_account_id = int(row.id)
        base_api.REPOSITORY.set_status("RUNNING", "")
        base_api.REPOSITORY.audit(
            "FINAL_PERSONAL_TRADING_STARTED",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {
                "managed_account_id": managed_account_id,
                "mode": body.mode,
                "recovery_state_reset": reset_recovery,
            },
        )
        return {
            "success": True,
            "state": "running",
            "lifecycle": "running",
            "enabled": True,
            "mode": body.mode,
            "recovery_reset": reset_recovery,
        }

    @app.post("/me/auto-trade")
    def final_auto_trade(request: Request, body: base_api.AutoTradeRequest) -> dict[str, Any]:
        if bool(body.enabled):
            return final_resume_trading(
                request,
                base_api.ResumeTradeRequest(mode="start_again"),
            )
        return final_stop_trading(request)

    @app.get("/me/trading-lifecycle")
    def final_trading_lifecycle(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            return {"authenticated": False, "lifecycle": "logged_out"}
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, int(account["id"]))
            if row is None:
                return {"authenticated": False, "lifecycle": "missing"}
            lifecycle = _lifecycle_from_row(row)
            # If a background writer produced an impossible disabled+running row,
            # self-heal it so the next refresh and the worker agree with the UI.
            status = str(row.execution_status or "inactive").strip().lower()
            if not bool(row.enabled) and status in RUNNING_STATUSES:
                row.execution_status = "stopped"
                row.execution_status_reason = (
                    "Auto trading is stopped. Start is required before execution."
                )[:160]
                row.execution_status_updated_at = utc_now()
                row.updated_at = utc_now()
                lifecycle = "stopped"
                status = "stopped"
            return {
                "authenticated": True,
                "lifecycle": lifecycle,
                "execution_status": status,
                "reason": str(row.execution_status_reason or ""),
                "enabled": bool(row.enabled),
            }

    @app.get("/me/trades/today")
    def final_personal_trades_today(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        start, end = _today_bounds_utc()
        with base_api.DATABASE.session() as session:
            rows = session.execute(
                select(Trade, CandidateSignalRecord, DirectionalSignal)
                .outerjoin(
                    CandidateSignalRecord,
                    CandidateSignalRecord.signal_id == Trade.signal_id,
                )
                .outerjoin(
                    DirectionalSignal,
                    DirectionalSignal.signal_id == Trade.signal_id,
                )
                .where(Trade.managed_account_id == int(account["id"]))
                .where(
                    or_(
                        Trade.purchase_time.between(start, end),
                        Trade.settlement_time.between(start, end),
                        Trade.provider_purchase_time.between(start, end),
                    )
                )
                .order_by(Trade.purchase_time.desc())
                .limit(5000)
            ).all()
            trades = [
                _trade_to_payload(trade, candidate, directional)
                for trade, candidate, directional in rows
            ]
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
            "timezone": str(_reporting_timezone()),
            "date": start.astimezone(_reporting_timezone()).date().isoformat(),
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

    @app.post("/me/clear-trades")
    def final_clear_personal_trades(request: Request, body: ClearTradesRequest) -> dict[str, Any]:
        account = _current_account_payload(request)
        scope = str(body.scope or "today").strip().lower()
        if scope not in {"today", "all"}:
            raise HTTPException(status_code=400, detail="scope must be today or all")
        start, end = _today_bounds_utc()
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, int(account["id"]), with_for_update=True)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")
            open_count = session.scalar(
                select(Trade.id)
                .where(Trade.managed_account_id == int(row.id))
                .where(Trade.settlement_time.is_(None))
                .limit(1)
            )
            if open_count is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot clear trades while an account contract is still open.",
                )
            trade_filter = Trade.managed_account_id == int(row.id)
            virtual_filter = VirtualTrade.managed_account_id == int(row.id)
            if scope == "today":
                trade_filter = trade_filter & or_(
                    Trade.purchase_time.between(start, end),
                    Trade.settlement_time.between(start, end),
                    Trade.provider_purchase_time.between(start, end),
                )
                virtual_filter = virtual_filter & or_(
                    VirtualTrade.created_at.between(start, end),
                    VirtualTrade.settled_at.between(start, end),
                )
            deleted_trades = len(session.scalars(select(Trade.id).where(trade_filter)).all())
            deleted_virtual = len(
                session.scalars(select(VirtualTrade.id).where(virtual_filter)).all()
            )
            session.execute(delete(Trade).where(trade_filter))
            session.execute(delete(VirtualTrade).where(virtual_filter))
            _reset_risk_state(session, int(row.id))
            _clear_account_runtime_preferences(session, int(row.id))
            row.execution_status_reason = (
                f"Personal {scope} trade history cleared; next Start begins from base state."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
        base_api.REPOSITORY.audit(
            "FINAL_PERSONAL_TRADES_CLEARED",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {
                "managed_account_id": int(account["id"]),
                "scope": scope,
                "deleted_trades": deleted_trades,
                "deleted_virtual_trades": deleted_virtual,
            },
        )
        return {
            "success": True,
            "scope": scope,
            "deleted_trades": deleted_trades,
            "deleted_virtual_trades": deleted_virtual,
            "message": f"Cleared {scope} personal trades and reset account recovery state.",
        }

    app.state.final_public_controls_installed = True
    _INSTALLED = True
