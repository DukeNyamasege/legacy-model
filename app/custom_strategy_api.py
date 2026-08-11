from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

import app.api as base_api
from app.custom_strategy_v1 import (
    COMPARATORS,
    DEFAULT_DURATION_TICKS,
    MAX_CONDITIONS,
    MAX_DURATION_TICKS,
    MAX_WINDOW,
    MIN_DURATION_TICKS,
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
from app.manual_martingale_v2 import (
    DEFAULT_MULTIPLIER,
    DEFAULT_SPLIT_COUNT,
    MAX_MULTIPLIER,
    PREFERENCE_PREFIX as MANUAL_MARTINGALE_PREFIX,
    SPLIT_REMAINING_PREFIX,
    normalize_manual_martingale_settings,
    read_manual_martingale_settings,
)
from app.models import RuntimePreference, Trade, VirtualTrade, utc_now
from app.strategy_v2_preferences import read_strategy, write_strategy


_INSTALLED = False


class CustomConditionRequest(BaseModel):
    kind: str
    window: int = Field(ge=1, le=MAX_WINDOW)
    parity: str | None = None
    operator: str | None = None
    value: int | None = Field(default=None, ge=0, le=9)
    direction: str | None = None
    target: str | None = None
    threshold: float | None = Field(default=None, ge=0, le=100)


class CustomMartingaleRequest(BaseModel):
    mode: str = Field(default="system", pattern="^(system|multiplier|split)$")
    multiplier: float = Field(
        default=DEFAULT_MULTIPLIER,
        ge=1.10,
        le=MAX_MULTIPLIER,
    )
    split_count: int = Field(default=DEFAULT_SPLIT_COUNT, ge=1, le=3)


class CustomVirtualHookRequest(BaseModel):
    enabled: bool = True
    enter_after_runs: int = Field(default=2, ge=1, le=50)
    exit_after_wins: int = Field(default=1, ge=1, le=50)


class CustomStrategyRequest(BaseModel):
    market_mode: str = "all"
    markets: list[str] = Field(default_factory=list)
    trade_type: str
    prediction: int | None = Field(default=None, ge=0, le=9)
    duration_ticks: int = Field(
        default=DEFAULT_DURATION_TICKS,
        ge=MIN_DURATION_TICKS,
        le=MAX_DURATION_TICKS,
    )
    conditions: list[CustomConditionRequest] = Field(
        min_length=1,
        max_length=MAX_CONDITIONS,
    )
    match: str = "all"
    reanalyze: dict[str, Any] | None = None
    virtual_hook_enabled: bool = True
    virtual_hook: CustomVirtualHookRequest | None = None
    martingale: CustomMartingaleRequest | None = None


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


def _write_runtime_preference(
    session: Any,
    key: str,
    value: str,
) -> None:
    row = session.get(RuntimePreference, str(key))
    if row is None:
        session.add(
            RuntimePreference(
                preference_key=str(key),
                preference_value=str(value),
            )
        )
        return
    row.preference_value = str(value)
    row.updated_at = utc_now()


def _write_custom_martingale(
    session: Any,
    managed_id: int,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Persist Custom Strategy recovery in the same DB transaction as its rule."""

    settings = normalize_manual_martingale_settings(raw)
    _write_runtime_preference(
        session,
        f"{MANUAL_MARTINGALE_PREFIX}{int(managed_id)}",
        json.dumps(settings, sort_keys=True, separators=(",", ":")),
    )
    # A newly saved strategy always begins with a fresh recovery plan. Historical
    # trades remain intact; only the in-progress split counter is reset.
    _write_runtime_preference(
        session,
        f"{SPLIT_REMAINING_PREFIX}{int(managed_id)}",
        "0",
    )
    return settings


def _install_custom_alert_matching() -> None:
    # The persisted account strategy has contract_type=CUSTOM while a qualified
    # custom candidate correctly persists its real financial contract (CALL, PUT,
    # DIGITEVEN, etc.). Match by the explicit Custom trigger instead of trying to
    # compare those intentionally different contract labels.
    import app.final_execution_alert_api as final_alert

    current = final_alert._matches_strategy
    if getattr(current, "_custom_strategy_matching", False):
        return

    def matches_with_custom(signal: Any, selection: Any) -> bool:
        if str(getattr(selection, "family", "") or "") == "custom":
            trigger = str(getattr(signal, "trigger_name", "") or "").upper()
            return trigger.startswith(("CUSTOM-V1-", "CUSTOM-V2-"))
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
        martingale = read_manual_martingale_settings(base_api.REPOSITORY, managed_id)
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
            "martingale": martingale,
            "preview": preview,
            "supported": {
                "markets": list(SUPPORTED_MARKETS),
                "market_modes": ["single", "selected", "all"],
                "trade_types": [
                    {
                        "value": value,
                        "label": str(meta["label"]),
                        "contract_type": str(meta["contract_type"]),
                    }
                    for value, meta in TRADE_TYPES.items()
                ],
                "comparators": list(COMPARATORS),
                "digit_comparators": list(COMPARATORS),
                "percentage_comparators": [
                    item for item in COMPARATORS if item != "all_same"
                ],
                "condition_types": [
                    "digit_parity",
                    "digit_compare",
                    "direction",
                    "percentage",
                ],
                "tick_directions": ["rising", "falling", "no_move"],
                "duration": {
                    "unit": "ticks",
                    "minimum": MIN_DURATION_TICKS,
                    "maximum": MAX_DURATION_TICKS,
                    "default": DEFAULT_DURATION_TICKS,
                },
                "martingale": {
                    "modes": ["system", "multiplier", "split"],
                    "default_multiplier": DEFAULT_MULTIPLIER,
                    "minimum_multiplier": 1.10,
                    "maximum_multiplier": MAX_MULTIPLIER,
                    "default_split_count": DEFAULT_SPLIT_COUNT,
                    "minimum_split_count": 1,
                    "maximum_split_count": 3,
                },
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
            "duration_ticks": body.duration_ticks,
            "conditions": [item.model_dump() for item in body.conditions],
            "match": body.match,
            "reanalyze": body.reanalyze or {},
            "virtual_hook_enabled": bool(body.virtual_hook_enabled),
            "virtual_hook": (
                body.virtual_hook.model_dump()
                if body.virtual_hook is not None
                else {"enabled": bool(body.virtual_hook_enabled)}
            ),
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
            previous_martingale = read_manual_martingale_settings(
                base_api.REPOSITORY,
                managed_id,
            )
            try:
                config = write_custom_strategy(session, managed_id, payload)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            _reset_risk_state(session, managed_id)
            _clear_account_runtime_preferences(session, managed_id)
            martingale = (
                _write_custom_martingale(
                    session,
                    managed_id,
                    body.martingale.model_dump(),
                )
                if body.martingale is not None
                else previous_martingale
            )
            # Even when the caller uses an older UI without the nested Martingale
            # object, reset any stale split progress while preserving its policy.
            if body.martingale is None:
                _write_runtime_preference(
                    session,
                    f"{SPLIT_REMAINING_PREFIX}{managed_id}",
                    "0",
                )
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
                    "duration_ticks": config["duration_ticks"],
                    "condition_count": len(config["conditions"]),
                    "condition_join": "AND",
                    "martingale_mode": martingale["mode"],
                    "martingale_multiplier": martingale["multiplier"],
                    "martingale_split_count": martingale["split_count"],
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
            "martingale": martingale,
            "preview": preview,
            "lifecycle": "stopped",
            "recovery_reset": True,
            "history_preserved": True,
            "message": (
                f"Custom Strategy saved with {config['duration_ticks']}-tick contract "
                f"duration and {martingale['mode']} recovery. Press Start to scan the "
                "selected markets continuously; no candidate is created until every "
                "condition matches."
            ),
        }

    app.state.custom_strategy_api_installed = True
    app.state.custom_strategy_api_version = "20260808-custom-card-v3"
    _INSTALLED = True
