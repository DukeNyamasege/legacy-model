from __future__ import annotations

"""Low-latency execution controls for the Full-VPS runtime.

The browser must never wait for recovery-history cleanup before a manual Stop is
acknowledged.  The persisted ManagedAccount lifecycle is the financial authority;
the worker's manual-stop guard re-reads that row before execution, proposal and
BUY.  These VPS routes therefore commit the smallest possible lifecycle mutation
first and keep reset work bounded/account-scoped.
"""

from inspect import isawaitable
from typing import Any, Callable

from fastapi import HTTPException, Request
from sqlalchemy import delete, or_, select

import app.api as base_api
from app.final_public_controls import ClearTradesRequest, _today_bounds_utc
from app.models import AccountRiskState, ManagedAccount, RuntimePreference, Trade, VirtualTrade, utc_now

_INSTALLED = False


def _remove_route(app: Any, path: str, method: str) -> Callable[..., Any] | None:
    expected = method.upper()
    captured: Callable[..., Any] | None = None
    retained = []
    for route in app.router.routes:
        if (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        ):
            endpoint = getattr(route, "endpoint", None)
            if callable(endpoint):
                captured = endpoint
            continue
        retained.append(route)
    app.router.routes[:] = retained
    return captured


def _current_account(request: Request) -> dict[str, Any]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return account


def _reset_risk_state_bounded(session: Any, managed_id: int) -> None:
    """Reset only the one account risk row; never scan unrelated account data."""

    state = session.get(AccountRiskState, int(managed_id))
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


def _delete_runtime_preferences_bounded(session: Any, managed_id: int) -> int:
    """Delete only this account's known recovery keys with one SQL DELETE.

    The previous public-control helper loaded the complete RuntimePreference table
    into Python and inspected every row.  That work happened while Stop held the
    ManagedAccount lock and was a major source of slow acknowledgements.
    """

    prefixes = (
        f"aidr_split_remaining:{managed_id}",
        f"aidr_adaptive_trap:{managed_id}",
        f"aidr_over1_over3_v1:account_epoch:{managed_id}",
        f"hybrid_over2_put_v4:account_epoch:{managed_id}",
        f"hybrid_o2u7_put_v1:account_epoch:{managed_id}",
    )
    result = session.execute(
        delete(RuntimePreference).where(
            or_(*(RuntimePreference.preference_key.like(f"{prefix}%") for prefix in prefixes))
        )
    )
    return int(result.rowcount or 0)


def _mark_stopped_now(request: Request, *, status: str, reason: str) -> tuple[int, str]:
    account = _current_account(request)
    managed_id = int(account["id"])
    # Deliberately avoid SELECT ... FOR UPDATE here.  The only authority needed to
    # stop new purchases is enabled=False + a terminal lifecycle status.  Keeping
    # this transaction tiny lets the manual-stop BUY guard observe it immediately.
    with base_api.DATABASE.session() as session:
        row = session.get(ManagedAccount, managed_id)
        if row is None:
            raise HTTPException(status_code=401, detail="Managed account was not found")
        row.enabled = False
        row.execution_status = status
        row.execution_status_reason = reason[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()
    try:
        base_api.mark_dashboard_dirty(account.get("account_type"))
    except Exception:
        pass
    return managed_id, str(account.get("account_type") or "demo")


def _audit(event: str, request: Request, payload: dict[str, Any]) -> None:
    try:
        base_api.REPOSITORY.audit(
            event,
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            payload,
        )
    except Exception:
        # Audit transport must never delay or invalidate a financial Stop.
        pass


def install_vps_fast_execution_controls(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Capture the current Custom Strategy auto-trade route so enabled=True keeps
    # the canonical start/preflight implementation.  Only enabled=False is replaced
    # by the immediate durable Stop authority below.
    original_auto = _remove_route(app, "/me/auto-trade", "POST")
    _remove_route(app, "/me/stop-trading", "POST")
    _remove_route(app, "/me/pause-trading", "POST")
    _remove_route(app, "/me/clear-trades", "POST")

    @app.post("/me/stop-trading")
    def fast_stop_trading(request: Request) -> dict[str, Any]:
        managed_id, _account_type = _mark_stopped_now(
            request,
            status="stopped",
            reason="Auto trading stopped. No new proposal or BUY is permitted until Start.",
        )
        _audit(
            "VPS_FAST_PERSONAL_TRADING_STOPPED",
            request,
            {"managed_account_id": managed_id, "new_purchase_authority": False},
        )
        return {
            "success": True,
            "state": "stopped",
            "lifecycle": "stopped",
            "runtime_state": "STOPPED",
            "enabled": False,
            "stop_acknowledged": True,
            "message": "Trading stopped. No new purchases are permitted.",
        }

    @app.post("/me/pause-trading")
    def fast_pause_trading(request: Request) -> dict[str, Any]:
        managed_id, _account_type = _mark_stopped_now(
            request,
            status="manual_pause",
            reason="Auto trading paused. No new proposal or BUY is permitted until Resume.",
        )
        _audit(
            "VPS_FAST_PERSONAL_TRADING_PAUSED",
            request,
            {"managed_account_id": managed_id, "new_purchase_authority": False},
        )
        return {
            "success": True,
            "state": "paused",
            "lifecycle": "paused",
            "runtime_state": "STOPPED",
            "enabled": False,
            "stop_acknowledged": True,
        }

    @app.post("/me/auto-trade")
    async def fast_auto_trade(request: Request, body: base_api.AutoTradeRequest) -> Any:
        if not bool(body.enabled):
            return fast_stop_trading(request)
        if original_auto is None:
            raise HTTPException(status_code=503, detail="Start execution route is unavailable")
        result = original_auto(request, body)
        return await result if isawaitable(result) else result

    @app.post("/me/clear-trades")
    def fast_clear_personal_trades(request: Request, body: ClearTradesRequest) -> dict[str, Any]:
        account = _current_account(request)
        managed_id = int(account["id"])
        scope = str(body.scope or "today").strip().lower()
        if scope not in {"today", "all"}:
            raise HTTPException(status_code=400, detail="scope must be today or all")
        start, end = _today_bounds_utc()

        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")
            open_contract = session.scalar(
                select(Trade.id)
                .where(Trade.managed_account_id == managed_id)
                .where(Trade.settlement_time.is_(None))
                .limit(1)
            )
            if open_contract is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Reset is available after the open contract settles.",
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

            trade_result = session.execute(delete(Trade).where(trade_filter))
            virtual_result = session.execute(delete(VirtualTrade).where(virtual_filter))
            _reset_risk_state_bounded(session, managed_id)
            removed_preferences = _delete_runtime_preferences_bounded(session, managed_id)
            row.execution_status_reason = (
                f"{scope.title()} run history reset from the main Run panel."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

        deleted_trades = int(trade_result.rowcount or 0)
        deleted_virtual = int(virtual_result.rowcount or 0)
        _audit(
            "VPS_FAST_PERSONAL_TRADES_CLEARED",
            request,
            {
                "managed_account_id": managed_id,
                "scope": scope,
                "deleted_trades": deleted_trades,
                "deleted_virtual_trades": deleted_virtual,
                "removed_runtime_preferences": removed_preferences,
            },
        )
        try:
            base_api.mark_dashboard_dirty(account.get("account_type"))
        except Exception:
            pass
        return {
            "success": True,
            "scope": scope,
            "deleted_trades": deleted_trades,
            "deleted_virtual_trades": deleted_virtual,
            "message": "Run panel reset complete.",
        }

    app.state.vps_fast_execution_controls_installed = True
    _INSTALLED = True
