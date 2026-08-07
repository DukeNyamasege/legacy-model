from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

import app.api as base_api
from app.custom_strategy_v1 import (
    COMPARATORS,
    MAX_CONDITIONS,
    MAX_WINDOW,
    SUPPORTED_MARKETS,
    TRADE_TYPES,
    describe_custom_strategy,
    read_custom_strategy,
    write_custom_strategy,
)
from app.final_public_controls import (
    STOPPED_STATUSES,
    _clear_account_runtime_preferences,
    _current_account_payload,
    _load_managed_account,
    _remove_route,
    _reset_risk_state,
)
from app.models import Trade, VirtualTrade, utc_now
from app.strategy_v2_preferences import read_strategy, write_strategy


_INSTALLED = False


class CustomConditionRequest(BaseModel):
    kind: str
    window: int = Field(ge=1, le=MAX_WINDOW)
    parity: str | None = None
    operator: str | None = None
    value: int | None = Field(default=None, ge=0, le=9)
    direction: str | None = None


class CustomStrategyRequest(BaseModel):
    market_mode: str = "all"
    markets: list[str] = Field(default_factory=list)
    trade_type: str
    prediction: int | None = Field(default=None, ge=0, le=9)
    conditions: list[CustomConditionRequest] = Field(
        min_length=1,
        max_length=MAX_CONDITIONS,
    )
    match: str = "all"


def _open_count(session: Any, managed_account_id: int) -> int:
    actual = int(
        session.scalar(
            select(func.count())
            .select_from(Trade)
            .where(
                Trade.managed_account_id == int(managed_account_id),
                Trade.settlement_time.is_(None),
            )
        )
        or 0
    )
    virtual = int(
        session.scalar(
            select(func.count())
            .select_from(VirtualTrade)
            .where(
                VirtualTrade.managed_account_id == int(managed_account_id),
                VirtualTrade.result == "OPEN",
            )
        )
        or 0
    )
    return actual + virtual


def _install_custom_alert_matching() -> None:
    # The persisted account strategy has contract_type=CUSTOM while a qualified
    # custom candidate correctly persists its real financial contract (CALL, PUT,
    # DIGITEVEN, etc.). Match by the explicit CUSTOM-V1 trigger instead of trying
    # to compare those intentionally different contract labels.
    import app.final_execution_alert_api as final_alert

    current = final_alert._matches_strategy
    if getattr(current, "_custom_strategy_matching", False):
        return

    def matches_with_custom(signal: Any, selection: Any) -> bool:
        if str(getattr(selection, "family", "") or "") == "custom":
            return str(getattr(signal, "trigger_name", "") or "").upper().startswith(
                "CUSTOM-V1-"
            )
        return bool(current(signal, selection))

    matches_with_custom._custom_strategy_matching = True  # type: ignore[attr-defined]
    final_alert._matches_strategy = matches_with_custom


def install_custom_strategy_api(app: Any) -> None:
    """Install the account-scoped Custom Strategy Builder API."""

    global _INSTALLED
    if _INSTALLED:
        return

    _install_custom_alert_matching()

    for path, method in (
        ("/me/custom-strategy", "GET"),
        ("/me/custom-strategy", "POST"),
    ):
        _remove_route(app, path, method)

    @app.get("/me/custom-strategy")
    def personal_custom_strategy(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        selection = read_strategy(base_api.DATABASE, managed_id)
        config = read_custom_strategy(base_api.DATABASE, managed_id)
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request)
            status = str(row.execution_status or "inactive").strip().lower()
            stopped = not bool(row.enabled) and status in STOPPED_STATUSES
            open_count = _open_count(session, managed_id)
        preview = ""
        if bool(config.get("configured")):
            try:
                preview = describe_custom_strategy(config)
            except ValueError:
                preview = ""
        return {
            "authenticated": True,
            "managed_account_id": managed_id,
            "active": str(selection.family) == "custom",
            "editable": bool(stopped and open_count == 0),
            "lifecycle": "stopped" if stopped else "running_or_paused",
            "open_contracts": open_count,
            "selection": selection.to_dict(),
            "config": config,
            "preview": preview,
            "supported": {
                "markets": list(SUPPORTED_MARKETS),
                "trade_types": [
                    {
                        "value": value,
                        "label": str(meta["label"]),
                        "contract_type": str(meta["contract_type"]),
                    }
                    for value, meta in TRADE_TYPES.items()
                ],
                "comparators": list(COMPARATORS),
                "condition_types": [
                    "digit_parity",
                    "digit_compare",
                    "direction",
                ],
                "maximum_window": MAX_WINDOW,
                "maximum_conditions": MAX_CONDITIONS,
                "condition_join": "AND",
            },
        }

    @app.post("/me/custom-strategy")
    def update_personal_custom_strategy(
        request: Request,
        body: CustomStrategyRequest,
    ) -> dict[str, Any]:
        payload = {
            "market_mode": body.market_mode,
            "markets": body.markets,
            "trade_type": body.trade_type,
            "prediction": body.prediction,
            "conditions": [item.model_dump() for item in body.conditions],
            "match": body.match,
        }

        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            status = str(row.execution_status or "inactive").strip().lower()
            if bool(row.enabled) or status not in STOPPED_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Stop AutoTrade completely before saving Custom Strategy. "
                        "Pause is not enough because a strategy change resets recovery state."
                    ),
                )
            open_count = _open_count(session, managed_id)
            if open_count:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Wait for {open_count} open actual/virtual contract(s) to settle "
                        "before changing Custom Strategy."
                    ),
                )

            previous = read_strategy(base_api.DATABASE, managed_id)
            try:
                config = write_custom_strategy(session, managed_id, payload)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            _reset_risk_state(session, managed_id)
            _clear_account_runtime_preferences(session, managed_id)
            selection = write_strategy(
                session,
                managed_id,
                family="custom",
                side="custom",
                prediction=None,
            )
            row.execution_status = "stopped"
            row.execution_status_reason = (
                "Custom Strategy saved. Pattern scanning begins after Start and only "
                "qualified AND-pattern matches can enter execution."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

        preview = describe_custom_strategy(config)
        try:
            base_api.REPOSITORY.audit(
                "PERSONAL_CUSTOM_STRATEGY_CHANGED",
                "personal_dashboard",
                request.client.host if request.client else "unknown",
                {
                    "managed_account_id": managed_id,
                    "previous_family": previous.family,
                    "previous_side": previous.side,
                    "market_mode": config["market_mode"],
                    "markets": config["markets"],
                    "trade_type": config["trade_type"],
                    "prediction": config["prediction"],
                    "condition_count": len(config["conditions"]),
                    "condition_join": "AND",
                    "recovery_state_reset": True,
                    "history_preserved": True,
                },
            )
        except Exception:
            base_api.LOGGER.exception(
                "CUSTOM_STRATEGY_AUDIT_FAILED managed_id=%s",
                managed_id,
            )
        return {
            "success": True,
            "selection": selection.to_dict(),
            "config": config,
            "preview": preview,
            "lifecycle": "stopped",
            "recovery_reset": True,
            "history_preserved": True,
            "message": (
                "Custom Strategy saved. Press Start to scan the selected markets "
                "continuously; no candidate is created until every condition matches."
            ),
        }

    app.state.custom_strategy_api_installed = True
    app.state.custom_strategy_api_version = "20260807-custom-v1"
    _INSTALLED = True
