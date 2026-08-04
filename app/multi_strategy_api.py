from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

import app.api as base_api
from app.final_public_controls import (
    STOPPED_STATUSES,
    _clear_account_runtime_preferences,
    _current_account_payload,
    _load_managed_account,
    _remove_route,
    _reset_risk_state,
)
from app.models import Trade, VirtualTrade, utc_now
from app.strategy_preferences import (
    normalize_strategy,
    read_strategy,
    strategy_catalog_payload,
    write_strategy,
)

_INSTALLED = False


class StrategySelectionRequest(BaseModel):
    family: str
    side: str


def _open_trade_count(session: Any, managed_account_id: int) -> int:
    actual = len(
        session.scalars(
            select(Trade.id).where(
                Trade.managed_account_id == int(managed_account_id),
                Trade.settlement_time.is_(None),
            )
        ).all()
    )
    virtual = len(
        session.scalars(
            select(VirtualTrade.id).where(
                VirtualTrade.managed_account_id == int(managed_account_id),
                VirtualTrade.result == "OPEN",
            )
        ).all()
    )
    return actual + virtual


def install_multi_strategy_api(app: Any) -> None:
    """Expose persistent per-account strategy selection.

    Strategy changes are deliberately separate from stake settings. A user must
    fully Stop AutoTrade first, ensuring debt from one contract family cannot be
    carried into another family. Existing trade rows remain untouched.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    for path, method in (
        ("/strategies/catalog", "GET"),
        ("/me/strategy-settings", "GET"),
        ("/me/strategy-settings", "POST"),
    ):
        _remove_route(app, path, method)

    @app.get("/strategies/catalog")
    def strategies_catalog() -> dict[str, Any]:
        return strategy_catalog_payload()

    @app.get("/me/strategy-settings")
    def personal_strategy_settings(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        selection = read_strategy(base_api.DATABASE, int(account["id"]))
        return {
            "authenticated": True,
            "managed_account_id": int(account["id"]),
            "selection": selection.to_dict(),
            "catalog": strategy_catalog_payload(),
        }

    @app.post("/me/strategy-settings")
    def update_personal_strategy_settings(
        request: Request,
        body: StrategySelectionRequest,
    ) -> dict[str, Any]:
        try:
            requested = normalize_strategy(body.family, body.side)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            status = str(row.execution_status or "inactive").strip().lower()
            if bool(row.enabled) or status not in STOPPED_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Stop AutoTrade completely before changing strategy. Pause is not "
                        "enough because strategy switching resets recovery state."
                    ),
                )
            open_count = _open_trade_count(session, managed_id)
            if open_count:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Wait for {open_count} open actual/virtual contract(s) to settle, "
                        "then save the strategy again."
                    ),
                )
            previous = read_strategy(base_api.DATABASE, managed_id)
            selection = write_strategy(
                session,
                managed_id,
                family=requested.family,
                side=requested.side,
            )
            _reset_risk_state(session, managed_id)
            _clear_account_runtime_preferences(session, managed_id)
            row.execution_status = "stopped"
            row.execution_status_reason = (
                f"Strategy changed to {selection.label}. Press Start to begin from base stake."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

        base_api.REPOSITORY.audit(
            "PERSONAL_STRATEGY_CHANGED",
            "personal_dashboard",
            request.client.host if request.client else "unknown",
            {
                "managed_account_id": managed_id,
                "previous_family": previous.family,
                "previous_side": previous.side,
                "new_family": selection.family,
                "new_side": selection.side,
                "contract_type": selection.contract_type,
                "recovery_state_reset": True,
                "history_preserved": True,
            },
        )
        return {
            "success": True,
            "selection": selection.to_dict(),
            "lifecycle": "stopped",
            "recovery_reset": True,
            "history_preserved": True,
            "message": (
                f"{selection.label} selected. Press Start to begin from base stake; "
                "previous trade history remains visible."
            ),
        }

    app.state.multi_strategy_api_installed = True
    _INSTALLED = True
