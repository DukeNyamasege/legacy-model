from __future__ import annotations

import json
import math
import time
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

import app.api as base_api
from app.custom_strategy_api import (
    CustomStrategyRequest,
    _custom_virtual_hook_payload,
    _open_count,
    _write_custom_martingale,
    _write_runtime_preference,
)
from app.custom_strategy_v1 import (
    PREFERENCE_PREFIX as CUSTOM_PREFERENCE_PREFIX,
    default_custom_strategy,
    describe_custom_strategy,
    normalize_custom_strategy,
    write_custom_strategy,
)
from app.final_public_controls import (
    STOPPED_STATUSES,
    _current_account_payload,
    _load_managed_account,
    _remove_route,
    _reset_risk_state,
)
from app.manual_martingale_v2 import (
    DEFAULT_MULTIPLIER,
    DEFAULT_SPLIT_COUNT,
    PREFERENCE_PREFIX as MANUAL_MARTINGALE_PREFIX,
    SPLIT_REMAINING_PREFIX,
    normalize_manual_martingale_settings,
)
from app.models import RuntimePreference, utc_now
from app.strategy_v2_preferences import (
    STRATEGY_KEY_PREFIX,
    _decode_payload,
    write_strategy,
)


_INSTALLED = False
_FATAL_STATUSES = {
    "error",
    "credential_error",
    "invalid_account",
    "token_required",
    "purchase_registration_error",
    "contract_unavailable",
}
_STARTING_STATUSES = {"starting", "connecting", "validating", "reconnecting"}
_WAITING_STATUSES = {"waiting_for_condition", "ready", "watching"}
_EXECUTING_STATUSES = {"executing", "purchasing", "proposal"}
_RUNNING_STATUSES = {
    "running",
    "active",
    "virtual_protection",
    "recovery_pending",
    "base_stake_protection",
}


class CustomExecutionSettingsRequest(BaseModel):
    stake_amount: float = Field(ge=0.35, le=1_000_000.0)
    take_profit: float = Field(default=0.0, ge=0.0, le=1_000_000.0)
    stop_loss: float = Field(default=0.0, ge=0.0, le=1_000_000.0)
    martingale_enabled: bool = True


class DirectCustomStrategyRequest(CustomStrategyRequest):
    execution_settings: CustomExecutionSettingsRequest | None = None


class DirectResumeRequest(BaseModel):
    mode: str = Field(default="start_again", pattern="^(start_again|continue)$")


def _runtime_state(*, enabled: bool, status: str) -> str:
    normalized = str(status or "inactive").strip().lower()
    if normalized in _FATAL_STATUSES:
        return "ERROR"
    if normalized in _STARTING_STATUSES:
        return "STARTING"
    if normalized in _WAITING_STATUSES:
        return "WAITING_FOR_CONDITION"
    if normalized in _EXECUTING_STATUSES:
        return "EXECUTING"
    if normalized in _RUNNING_STATUSES and enabled:
        return "RUNNING"
    return "STOPPED"


def _strategy_from_session(session: Any, managed_id: int):
    row = session.get(RuntimePreference, f"{STRATEGY_KEY_PREFIX}{int(managed_id)}")
    raw = str(row.preference_value or "") if row else ""
    return _decode_payload(raw)


def _custom_config_from_session(session: Any, managed_id: int) -> dict[str, Any]:
    row = session.get(
        RuntimePreference,
        f"{CUSTOM_PREFERENCE_PREFIX}{int(managed_id)}",
    )
    raw = str(row.preference_value or "") if row else ""
    if not raw:
        return default_custom_strategy()
    try:
        return normalize_custom_strategy(json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default_custom_strategy()


def _martingale_from_session(session: Any, managed_id: int) -> dict[str, Any]:
    row = session.get(
        RuntimePreference,
        f"{MANUAL_MARTINGALE_PREFIX}{int(managed_id)}",
    )
    raw = str(row.preference_value or "") if row else ""
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    return normalize_manual_martingale_settings(payload)


def _finite_money(value: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise HTTPException(status_code=400, detail=f"{label} must be a finite number.")
    return round(number, 2)


def _preflight_custom_start(session: Any, request: Request) -> tuple[Any, dict[str, Any]]:
    row = _load_managed_account(session, request, for_update=True)
    account = _current_account_payload(request)
    if not bool(account.get("has_trading_api_token", False)):
        raise HTTPException(
            status_code=409,
            detail="Trading stopped: connect a valid Deriv trade-scope credential before Start.",
        )
    config = _custom_config_from_session(session, int(row.id))
    if not bool(config.get("configured")):
        raise HTTPException(
            status_code=409,
            detail="Trading stopped: save a valid Custom Strategy before Start.",
        )
    open_count = _open_count(session, int(row.id))
    if open_count:
        raise HTTPException(
            status_code=409,
            detail=f"Trading cannot start while {open_count} contract(s) are still open.",
        )
    return row, config


def install_custom_strategy_runtime_api(app: Any) -> None:
    """Make backend execution readiness authoritative for the builder UI."""

    global _INSTALLED
    if _INSTALLED:
        return

    for path, method in (
        ("/me/custom-strategy", "POST"),
        ("/me/resume-trading", "POST"),
        ("/me/auto-trade", "POST"),
        ("/me/trading-lifecycle", "GET"),
        ("/me/execution-runtime", "GET"),
        ("/me/execution-alert", "GET"),
    ):
        _remove_route(app, path, method)

    @app.post("/me/custom-strategy")
    def save_custom_strategy_once(
        request: Request,
        body: DirectCustomStrategyRequest,
    ) -> dict[str, Any]:
        started = time.perf_counter()
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
            "virtual_hook": _custom_virtual_hook_payload(
                body.virtual_hook,
                enabled=bool(body.virtual_hook_enabled),
            ),
        }

        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            status = str(row.execution_status or "inactive").strip().lower()
            if bool(row.enabled) or status not in STOPPED_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail="Stop Auto Trading completely before saving Custom Strategy.",
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

            # All reads/writes below share this transaction. The former route held
            # this FOR UPDATE lock while opening nested database sessions.
            previous = _strategy_from_session(session, managed_id)
            previous_martingale = _martingale_from_session(session, managed_id)
            try:
                config = write_custom_strategy(session, managed_id, payload)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            settings = body.execution_settings
            if settings is not None:
                row.stake_amount = _finite_money(settings.stake_amount, "Stake amount")
                row.take_profit = _finite_money(settings.take_profit, "Take profit")
                row.stop_loss = _finite_money(abs(settings.stop_loss), "Stop loss")
                row.martingale_enabled = bool(settings.martingale_enabled)

            _reset_risk_state(session, managed_id)
            martingale = (
                _write_custom_martingale(
                    session,
                    managed_id,
                    body.martingale.model_dump(),
                )
                if body.martingale is not None
                else previous_martingale
            )
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
            row.enabled = False
            row.execution_status = "stopped"
            row.execution_status_reason = (
                "Custom Strategy saved. Press Start to initialize the account execution session."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        base_api.LOGGER.info(
            "CUSTOM_STRATEGY_SAVE_TIMING managed_id=%s duration_ms=%.2f "
            "write_requests=1 nested_sessions=0",
            managed_id,
            duration_ms,
        )
        preview = describe_custom_strategy(config)
        try:
            base_api.mark_dashboard_dirty(_current_account_payload(request).get("account_type"))
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
                    "duration_ticks": config["duration_ticks"],
                    "condition_count": len(config["conditions"]),
                    "martingale_mode": martingale["mode"],
                    "save_duration_ms": duration_ms,
                    "write_requests": 1,
                },
            )
        except Exception:
            base_api.LOGGER.exception(
                "CUSTOM_STRATEGY_POST_SAVE_AUDIT_FAILED managed_id=%s",
                managed_id,
            )
        return {
            "success": True,
            "selection": selection.to_dict(),
            "config": config,
            "martingale": martingale,
            "preview": preview,
            "lifecycle": "stopped",
            "runtime_state": "STOPPED",
            "save_duration_ms": duration_ms,
            "write_requests": 1,
            "message": "Custom Strategy saved. Press Start to initialize execution.",
        }

    @app.post("/me/resume-trading")
    def start_custom_runtime(
        request: Request,
        body: DirectResumeRequest,
    ) -> dict[str, Any]:
        with base_api.DATABASE.session() as session:
            row, config = _preflight_custom_start(session, request)
            managed_id = int(row.id)
            if body.mode == "start_again":
                _reset_risk_state(session, managed_id)
            row.enabled = True
            row.execution_status = "starting"
            row.execution_status_reason = (
                "Initializing authenticated account execution session. Scanning has not started yet."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
        base_api.LOGGER.info(
            "CUSTOM_RUNTIME_START_REQUESTED managed_id=%s markets=%s "
            "backend_state=STARTING scanner_started=false",
            managed_id,
            ",".join(config.get("markets") or ["ALL"]),
        )
        return {
            "success": True,
            "enabled": True,
            "state": "starting",
            "lifecycle": "running",
            "runtime_state": "STARTING",
            "message": "Initializing account execution session. Trading is not RUNNING yet.",
        }

    @app.post("/me/auto-trade")
    def custom_auto_trade(request: Request, body: base_api.AutoTradeRequest) -> dict[str, Any]:
        if bool(body.enabled):
            return start_custom_runtime(request, DirectResumeRequest(mode="start_again"))
        # Reuse the final stop semantics (recovery reset) without calling a removed
        # route closure: persist the authoritative stopped state directly.
        with base_api.DATABASE.session() as session:
            row = _load_managed_account(session, request, for_update=True)
            managed_id = int(row.id)
            row.enabled = False
            row.execution_status = "stopped"
            row.execution_status_reason = "Auto trading stopped. Account runtime will be destroyed."
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            _reset_risk_state(session, managed_id)
        return {
            "success": True,
            "enabled": False,
            "state": "stopped",
            "lifecycle": "stopped",
            "runtime_state": "STOPPED",
        }

    @app.get("/me/execution-runtime")
    def custom_execution_runtime(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            return {"authenticated": False, "runtime_state": "STOPPED"}
        with base_api.DATABASE.session() as session:
            row = session.get(base_api.ManagedAccount, int(account["id"])) if hasattr(base_api, "ManagedAccount") else None
            if row is None:
                from app.models import ManagedAccount

                row = session.get(ManagedAccount, int(account["id"]))
            if row is None:
                return {"authenticated": False, "runtime_state": "STOPPED"}
            status = str(row.execution_status or "inactive").strip().lower()
            enabled = bool(row.enabled)
            state = _runtime_state(enabled=enabled, status=status)
            return {
                "authenticated": True,
                "enabled": enabled,
                "runtime_state": state,
                "execution_status": status,
                "reason": str(row.execution_status_reason or ""),
                "fatal": state == "ERROR",
                "updated_at": (
                    row.execution_status_updated_at.isoformat()
                    if row.execution_status_updated_at
                    else None
                ),
            }

    @app.get("/me/trading-lifecycle")
    def custom_trading_lifecycle(request: Request) -> dict[str, Any]:
        payload = custom_execution_runtime(request)
        if not payload.get("authenticated"):
            return {"authenticated": False, "lifecycle": "logged_out", **payload}
        state = str(payload.get("runtime_state") or "STOPPED")
        lifecycle = "running" if state in {
            "STARTING",
            "WAITING_FOR_CONDITION",
            "EXECUTING",
            "RUNNING",
        } else "stopped"
        return {**payload, "lifecycle": lifecycle}

    @app.get("/me/execution-alert")
    def lightweight_execution_alert(request: Request) -> dict[str, Any]:
        # Builder runtime no longer scans 160 global candidate rows + decisions +
        # trades on every poll. Exact execution state is already account-scoped.
        payload = custom_execution_runtime(request)
        return {
            "authenticated": bool(payload.get("authenticated")),
            "alert": None,
            "runtime_state": payload.get("runtime_state", "STOPPED"),
            "execution_status": payload.get("execution_status", "inactive"),
            "reason": payload.get("reason", ""),
        }

    app.state.custom_strategy_runtime_api_installed = True
    app.state.custom_strategy_runtime_api_version = "20260812-direct-runtime-v1"
    _INSTALLED = True
