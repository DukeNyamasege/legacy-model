from __future__ import annotations

from typing import Any

from fastapi import Request

import app.api as base_api
from app.custom_strategy_runtime_api import (
    DirectResumeRequest,
    _preflight_custom_start,
    _runtime_state,
)
from app.final_public_controls import _current_account_payload, _remove_route, _reset_risk_state
from app.models import AccountRiskState, ManagedAccount, utc_now
from app.session_risk_limits import (
    read_session_risk_limits,
    snapshot_session_risk_limits,
)


_INSTALLED = False


def install_session_risk_api_authority(app: Any) -> None:
    """Expose one exact signed TP/SL session contract to every browser.

    A fresh Start freezes the currently saved settings. TP is the positive stated
    amount and SL is the negative stated amount. The lifecycle endpoint reports the
    same snapshot that the worker enforces, so notifications cannot mix a stale
    target with another session's P/L.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/resume-trading", "POST")
    _remove_route(app, "/me/trading-lifecycle", "GET")

    @app.post("/me/resume-trading")
    def start_with_session_limits(
        request: Request,
        body: DirectResumeRequest,
    ) -> dict[str, Any]:
        account = _current_account_payload(request)
        with base_api.DATABASE.session() as session:
            row, config = _preflight_custom_start(session, request, account)
            managed_id = int(row.id)
            if body.mode == "start_again":
                _reset_risk_state(session, managed_id)
                limits = snapshot_session_risk_limits(session, row)
            else:
                limits = read_session_risk_limits(session, managed_id, account=row)
            row.enabled = True
            row.execution_status = "starting"
            row.execution_status_reason = (
                "Initializing authenticated account execution session. Scanning has not started yet."
            )[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

        base_api.LOGGER.info(
            "CUSTOM_RUNTIME_START_REQUESTED managed_id=%s markets=%s "
            "take_profit=%.2f stop_loss=%.2f signed_limits=true",
            managed_id,
            ",".join(config.get("markets") or ["ALL"]),
            limits.take_profit,
            limits.stop_loss,
        )
        return {
            "success": True,
            "enabled": True,
            "state": "starting",
            "lifecycle": "running",
            "runtime_state": "STARTING",
            "take_profit": limits.take_profit,
            "stop_loss": limits.stop_loss,
            "session_limits_started_at": limits.started_at,
            "message": "Initializing account execution session. Trading is not RUNNING yet.",
        }

    @app.get("/me/trading-lifecycle")
    def exact_session_lifecycle(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            return {
                "authenticated": False,
                "lifecycle": "logged_out",
                "runtime_state": "STOPPED",
            }

        managed_id = int(account["id"])
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id)
            if row is None:
                return {
                    "authenticated": False,
                    "lifecycle": "missing",
                    "runtime_state": "STOPPED",
                }
            state = session.get(AccountRiskState, managed_id)
            limits = read_session_risk_limits(session, managed_id, account=row)
            status = str(row.execution_status or "inactive").strip().lower()
            enabled = bool(row.enabled)
            runtime_state = _runtime_state(enabled=enabled, status=status)
            session_profit = round(float(state.session_profit or 0.0), 2) if state else 0.0

        lifecycle = "running" if runtime_state in {
            "STARTING",
            "WAITING_FOR_CONDITION",
            "EXECUTING",
            "RUNNING",
        } else "stopped"
        if status == "take_profit":
            limit_target = limits.take_profit
            limit_achieved = session_profit
        elif status == "stop_loss":
            limit_target = limits.stop_loss
            limit_achieved = session_profit
        else:
            limit_target = 0.0
            limit_achieved = 0.0

        return {
            "authenticated": True,
            "enabled": enabled,
            "lifecycle": lifecycle,
            "runtime_state": runtime_state,
            "execution_status": status,
            "reason": str(row.execution_status_reason or ""),
            "session_profit": session_profit,
            "take_profit": limits.take_profit,
            "stop_loss": limits.stop_loss,
            "limit_target": limit_target,
            "limit_achieved": limit_achieved,
            "risk_limit_is_hard_stop": status in {"take_profit", "stop_loss"},
            "session_limits_started_at": limits.started_at,
        }

    app.state.session_risk_api_authority_installed = True
    _INSTALLED = True
