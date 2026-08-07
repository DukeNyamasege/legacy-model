from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

import app.api as base_api
from app.manual_martingale_v2 import (
    DEFAULT_MULTIPLIER,
    DEFAULT_SPLIT_COUNT,
    MAX_MULTIPLIER,
    REAL_RECOVERY_PENDING,
    SPLIT_MODE,
    VIRTUAL_WAITING_FOR_WIN,
    _STOPPED_STATUSES,
    _read_split_remaining,
    _write_split_remaining,
    read_manual_martingale_settings,
    save_manual_martingale_settings,
)
from app.models import AccountRiskState, ManagedAccount, Trade, VirtualTrade
from app.strategy_v2_preferences import read_strategy


_INSTALLED = False


class ManualMartingaleV2Request(BaseModel):
    mode: str = Field(pattern="^(system|multiplier|split)$")
    multiplier: float = Field(default=DEFAULT_MULTIPLIER, ge=1.10, le=MAX_MULTIPLIER)
    split_count: int = Field(default=DEFAULT_SPLIT_COUNT, ge=1, le=3)


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


def _current_payload(request: Request) -> tuple[dict[str, Any], Any, dict[str, Any], dict[str, Any]]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    managed_id = int(account["id"])
    selection = read_strategy(base_api.DATABASE, managed_id)
    settings = read_manual_martingale_settings(base_api.REPOSITORY, managed_id)
    with base_api.DATABASE.session() as session:
        row = session.get(ManagedAccount, managed_id)
        state = session.get(AccountRiskState, managed_id)
        if row is None:
            raise HTTPException(status_code=401, detail="Managed account was not found")
        status = str(row.execution_status or "inactive").strip().lower()
        stopped = not bool(row.enabled) and status in _STOPPED_STATUSES
        debt = float(state.recovery_loss_debt or 0.0) if state is not None else 0.0
        recovery_active = bool(
            state is not None
            and debt > 0.009
            and (
                state.recovery_pending
                or state.recovery_attempt_active
                or state.protection_mode in {REAL_RECOVERY_PENDING, VIRTUAL_WAITING_FOR_WIN}
            )
        )
    progress = {
        "lifecycle": "stopped" if stopped else "running_or_paused",
        "editable": bool(stopped and not recovery_active),
        "recovery_active": recovery_active,
        "recovery_debt": round(debt, 2),
        "split_remaining": (
            _read_split_remaining(base_api.REPOSITORY, managed_id)
            if recovery_active and settings["mode"] == SPLIT_MODE
            else 0
        ),
    }
    return account, selection, settings, progress


def install_manual_martingale_v2_final_api(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for path, method in (
        ("/me/manual-martingale", "GET"),
        ("/me/manual-martingale", "POST"),
    ):
        _remove_route(app, path, method)

    @app.get("/me/manual-martingale")
    def personal_manual_martingale(request: Request) -> dict[str, Any]:
        account, selection, settings, progress = _current_payload(request)
        return {
            "authenticated": True,
            "managed_account_id": int(account["id"]),
            "applicable": str(selection.family) != "system",
            "selection": selection.to_dict(),
            "settings": settings,
            **progress,
            "system_strategy_locked": str(selection.family) == "system",
        }

    @app.post("/me/manual-martingale")
    def update_personal_manual_martingale(
        request: Request,
        body: ManualMartingaleV2Request,
    ) -> dict[str, Any]:
        account, selection, _current, progress = _current_payload(request)
        managed_id = int(account["id"])
        if str(selection.family) == "system":
            raise HTTPException(
                status_code=409,
                detail=(
                    "System Strategy always uses its built-in System Martingale. "
                    "Choose Over/Under, Even/Odd or Rise/Fall before overriding recovery."
                ),
            )
        if not bool(progress["editable"]):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Stop AutoTrade completely and finish the active recovery cycle "
                    "before changing the Martingale policy."
                ),
            )

        with base_api.DATABASE.session() as session:
            open_actual = int(
                session.scalar(
                    select(func.count())
                    .select_from(Trade)
                    .where(
                        Trade.managed_account_id == managed_id,
                        Trade.settlement_time.is_(None),
                    )
                )
                or 0
            )
            open_virtual = int(
                session.scalar(
                    select(func.count())
                    .select_from(VirtualTrade)
                    .where(
                        VirtualTrade.managed_account_id == managed_id,
                        VirtualTrade.result == "OPEN",
                    )
                )
                or 0
            )
        if open_actual + open_virtual:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Wait for {open_actual + open_virtual} open actual/virtual contract(s) "
                    "to settle before changing Martingale."
                ),
            )

        settings = save_manual_martingale_settings(
            base_api.REPOSITORY,
            managed_id,
            {
                "mode": body.mode,
                "multiplier": body.multiplier,
                "split_count": body.split_count,
            },
        )
        _write_split_remaining(base_api.REPOSITORY, managed_id, 0)
        try:
            base_api.REPOSITORY.audit(
                "MANUAL_MARTINGALE_V2_CHANGED",
                "personal_dashboard",
                request.client.host if request.client else "unknown",
                {
                    "managed_account_id": managed_id,
                    "strategy_family": str(selection.family),
                    "strategy_side": str(selection.side),
                    "mode": settings["mode"],
                    "multiplier": settings["multiplier"],
                    "split_count": settings["split_count"],
                },
            )
        except Exception:
            base_api.LOGGER.exception(
                "MANUAL_MARTINGALE_V2_AUDIT_FAILED managed_id=%s",
                managed_id,
            )
        return {
            "success": True,
            "settings": settings,
            "selection": selection.to_dict(),
            "lifecycle": "stopped",
            "message": "Manual strategy Martingale saved. Press Start when you are ready.",
        }

    app.state.manual_martingale_v2_api_installed = True
    app.state.manual_martingale_v2_api_version = "20260807-1"
    _INSTALLED = True
