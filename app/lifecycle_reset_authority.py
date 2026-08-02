from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import delete, or_, select

import app.api as base_api
from app.final_public_controls import (
    PAUSED_STATUSES,
    STOPPED_STATUSES,
    ClearTradesRequest,
    _clear_account_runtime_preferences,
    _current_account_payload,
    _load_managed_account,
    _remove_route,
    _reset_risk_state,
    _today_bounds_utc,
)
from app.models import AccountRiskState, ManagedAccount, RuntimePreference, Trade, VirtualTrade, utc_now

_INSTALLED = False
_RESET_MARKER_PREFIX = "aidr_hard_reset_at:"


def _write_reset_marker(session: Any, managed_account_id: int) -> None:
    """Record an explicit history reset, never an ordinary Stop or Start."""
    key = f"{_RESET_MARKER_PREFIX}{int(managed_account_id)}"
    value = datetime.now(timezone.utc).isoformat()
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=value))
    else:
        row.preference_value = value
        row.updated_at = utc_now()


def _open_provider_trade_id(session: Any, managed_account_id: int) -> int | None:
    value = session.scalar(
        select(Trade.id)
        .where(
            Trade.managed_account_id == int(managed_account_id),
            Trade.settlement_time.is_(None),
        )
        .order_by(Trade.purchase_time.desc())
        .limit(1)
    )
    return int(value) if value is not None else None


def _cancel_open_virtual_trades(session: Any, managed_account_id: int, reason: str) -> int:
    rows = session.scalars(
        select(VirtualTrade)
        .where(
            VirtualTrade.managed_account_id == int(managed_account_id),
            VirtualTrade.result == "OPEN",
        )
        .with_for_update()
    ).all()
    now = utc_now()
    for row in rows:
        row.result = "VIRTUAL_CANCELLED_STOP"
        row.reason = str(reason or "Virtual observation cancelled by Stop/Reset")[:200]
        row.amount_charged = 0.0
        row.actual_profit_loss = 0.0
        row.actual_payout = 0.0
        row.recovery_debt_change = 0.0
        row.settled_at = now
    return len(rows)


def _hard_stop(
    session: Any,
    row: ManagedAccount,
    *,
    reason: str,
    mark_history_reset: bool = False,
) -> int:
    """Stop execution and reset active AIDR state without hiding trade history."""
    cancelled = _cancel_open_virtual_trades(session, int(row.id), reason)
    _reset_risk_state(session, int(row.id))
    _clear_account_runtime_preferences(session, int(row.id))
    if mark_history_reset:
        # Only the explicit Clear Today / Clear All action owns history resets.
        _write_reset_marker(session, int(row.id))
    row.enabled = False
    row.execution_status = "stopped"
    row.execution_status_reason = str(reason or "Auto trading stopped; next Start is fresh")[:160]
    row.execution_status_updated_at = utc_now()
    row.updated_at = utc_now()
    return cancelled


def _pause(session: Any, row: ManagedAccount) -> None:
    row.enabled = False
    row.execution_status = "manual_pause"
    row.execution_status_reason = (
        "Auto trading paused. Trade, debt and virtual-win progress are preserved for Resume."
    )[:160]
    row.execution_status_updated_at = utc_now()
    row.updated_at = utc_now()


def _start(session: Any, row: ManagedAccount, *, fresh: bool) -> None:
    if fresh:
        open_trade_id = _open_provider_trade_id(session, int(row.id))
        if open_trade_id is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A previous provider contract is still settling. Auto Trade remains stopped; "
                    "wait a few seconds and press Start again."
                ),
            )
        _cancel_open_virtual_trades(
            session,
            int(row.id),
            "Old virtual observation cancelled because a fresh Start was requested",
        )
        _reset_risk_state(session, int(row.id))
        _clear_account_runtime_preferences(session, int(row.id))
        # A fresh Start resets recovery state only. It must not hide or delete
        # contracts already executed on this account.
    row.enabled = True
    row.execution_status = "connecting"
    row.execution_status_reason = (
        "Fresh Auto Trade start from base stake; previous trade history is retained."
        if fresh
        else "Auto trading resumed with preserved recovery and virtual-win state."
    )[:160]
    row.execution_status_updated_at = utc_now()
    row.updated_at = utc_now()


def install_lifecycle_reset_authority(app: Any) -> None:
    """Install the final Pause/Stop/Reset contract for every personal account.

    Pause -> Resume preserves active recovery state.
    Stop -> Start resets active recovery state but retains all trade history.
    Reset Today/All is the only operation that deletes the selected history and
    leaves the account stopped so the worker cannot immediately repopulate it.
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
        ("/me/clear-trades", "POST"),
    ):
        _remove_route(app, path, method)

    @app.post("/me/stop-trading")
    def authoritative_stop(request: Request) -> dict[str, Any]:
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            cancelled = _hard_stop(
                session,
                row,
                reason=(
                    "Auto trading stopped. Recovery debt, pending recovery and virtual-win "
                    "progress were cleared. Trade history remains visible, and the next Start "
                    "begins at base stake."
                ),
                mark_history_reset=False,
            )
        base_api.REPOSITORY.audit(
            "AUTHORITATIVE_PERSONAL_STOP",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {
                "managed_account_id": managed_id,
                "fresh_next_start": True,
                "cancelled_open_virtual": cancelled,
                "history_preserved": True,
            },
        )
        return {
            "success": True,
            "state": "stopped",
            "lifecycle": "stopped",
            "enabled": False,
            "recovery_reset": True,
            "virtual_progress_reset": True,
            "history_preserved": True,
            "cancelled_open_virtual": cancelled,
            "message": (
                "Auto trading stopped. Trade history is retained; the next Start begins "
                "fresh from base stake."
            ),
        }

    @app.post("/me/pause-trading")
    def authoritative_pause(request: Request) -> dict[str, Any]:
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            _pause(session, row)
        base_api.REPOSITORY.audit(
            "AUTHORITATIVE_PERSONAL_PAUSE",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {
                "managed_account_id": managed_id,
                "recovery_state_preserved": True,
                "virtual_progress_preserved": True,
                "history_preserved": True,
            },
        )
        return {
            "success": True,
            "state": "paused",
            "lifecycle": "paused",
            "enabled": False,
            "recovery_preserved": True,
            "virtual_progress_preserved": True,
            "history_preserved": True,
            "message": "Paused. Resume continues from the preserved state.",
        }

    @app.post("/me/resume-trading")
    def authoritative_resume(
        request: Request,
        body: base_api.ResumeTradeRequest,
    ) -> dict[str, Any]:
        account = _current_account_payload(request)
        if not account.get("has_trading_api_token", False):
            raise HTTPException(
                status_code=409,
                detail="Save a Deriv API token before starting auto trading.",
            )
        requested_mode = str(body.mode or "continue").strip().lower()
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            previous = str(row.execution_status or "inactive").strip().lower()
            # Continue is valid only from an intentional pause. Any stopped state
            # always starts fresh, even if an old browser sends mode=continue.
            fresh = requested_mode == "start_again" or previous in STOPPED_STATUSES
            _start(session, row, fresh=fresh)
            managed_id = int(row.id)
        base_api.REPOSITORY.set_status("RUNNING", "")
        base_api.REPOSITORY.audit(
            "AUTHORITATIVE_PERSONAL_START",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {
                "managed_account_id": managed_id,
                "requested_mode": requested_mode,
                "fresh_start": fresh,
                "history_preserved": True,
            },
        )
        return {
            "success": True,
            "state": "running",
            "lifecycle": "running",
            "enabled": True,
            "mode": "start_again" if fresh else "continue",
            "fresh_start": fresh,
            "recovery_reset": fresh,
            "history_preserved": True,
        }

    @app.post("/me/auto-trade")
    def authoritative_auto_trade(
        request: Request,
        body: base_api.AutoTradeRequest,
    ) -> dict[str, Any]:
        if bool(body.enabled):
            return authoritative_resume(
                request,
                base_api.ResumeTradeRequest(mode="start_again"),
            )
        return authoritative_stop(request)

    @app.get("/me/trading-lifecycle")
    def authoritative_lifecycle(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            return {"authenticated": False, "lifecycle": "logged_out"}
        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id)
            state = session.get(AccountRiskState, managed_id)
            if row is None:
                return {"authenticated": False, "lifecycle": "missing"}
            status = str(row.execution_status or "inactive").strip().lower()
            if status in STOPPED_STATUSES:
                lifecycle = "stopped"
            elif not bool(row.enabled) or status in PAUSED_STATUSES:
                lifecycle = "paused"
            else:
                lifecycle = "running"
            return {
                "authenticated": True,
                "lifecycle": lifecycle,
                "execution_status": status,
                "reason": str(row.execution_status_reason or ""),
                "enabled": bool(row.enabled),
                "recovery_debt": round(float(state.recovery_loss_debt or 0.0), 2) if state else 0.0,
                "protection_mode": str(state.protection_mode or "NORMAL_MODE") if state else "NORMAL_MODE",
                "virtual_wins": int(state.virtual_win_count or 0) if state else 0,
                "virtual_wins_required": 2,
                "consecutive_losses": int(state.consecutive_losses or 0) if state else 0,
                "history_preserved_on_stop": True,
            }

    @app.post("/me/clear-trades")
    def authoritative_clear(
        request: Request,
        body: ClearTradesRequest,
    ) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        scope = str(body.scope or "today").strip().lower()
        if scope not in {"today", "all"}:
            raise HTTPException(status_code=400, detail="scope must be today or all")
        start, end = _today_bounds_utc()

        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id, with_for_update=True)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")
            open_trade = _open_provider_trade_id(session, managed_id)
            if open_trade is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot reset while an actual provider contract is still open.",
                )

            trade_filter = Trade.managed_account_id == managed_id
            virtual_filter = VirtualTrade.managed_account_id == managed_id
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
            cancelled = _hard_stop(
                session,
                row,
                reason=(
                    f"Reset {scope} completed. Selected actual and virtual history was cleared; "
                    "all recovery state was forgotten. Press Start for a fresh base-stake session."
                ),
                mark_history_reset=True,
            )

        base_api.REPOSITORY.audit(
            "AUTHORITATIVE_PERSONAL_RESET",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {
                "managed_account_id": managed_id,
                "scope": scope,
                "deleted_trades": deleted_trades,
                "deleted_virtual_trades": deleted_virtual,
                "cancelled_open_virtual": cancelled,
                "account_left_stopped": True,
            },
        )
        return {
            "success": True,
            "scope": scope,
            "deleted_trades": deleted_trades,
            "deleted_virtual_trades": deleted_virtual,
            "lifecycle": "stopped",
            "enabled": False,
            "recovery_reset": True,
            "virtual_progress_reset": True,
            "message": (
                f"Reset {scope} completed. Everything in that history scope and all recovery "
                "state were forgotten. Press Start to begin fresh."
            ),
        }

    app.state.lifecycle_reset_authority_installed = True
    _INSTALLED = True
