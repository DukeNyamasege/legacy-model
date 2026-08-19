from __future__ import annotations

from app.route_utils import remove_route as _remove_route

"""Full-VPS cleanup/performance authority for the global recovery policy."""

import json
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select

import app.api as base_api
from app.account_identity_canonical_authority import (
    install_account_identity_canonical_authority,
)
from app.direct_execution_hard_stop_state import direct_hard_stop_key
from app.direct_execution_lease import (
    DIRECT_BROWSER_STATUS,
    direct_browser_lease_remaining_seconds,
)
from app.models import AccountRiskState, ManagedAccount, RuntimePreference, utc_now


_INSTALLED = False
_OWNER_PREFIX = "direct_execution:v1:"
_CHECKPOINT_PREFIX = "direct_execution:checkpoint:v1:"
_SPLIT_BASIS_PREFIX = "custom_equal_split_basis_debt:"
_SPLIT_PART_STAKE_PREFIX = "custom_equal_split_part_stake:"
_SPLIT_REMAINING_PREFIX = "manual_martingale_v2_split_remaining:"




def _account(request: Request) -> dict[str, Any] | None:
    try:
        return base_api.get_current_account(request)
    except Exception:
        return None


def _reset_recovery_state(session: Any, managed_id: int) -> None:
    state = session.get(AccountRiskState, int(managed_id), with_for_update=True)
    if state is not None:
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

    exact_keys = (
        f"{_CHECKPOINT_PREFIX}{managed_id}",
        f"{_SPLIT_BASIS_PREFIX}{managed_id}",
        f"{_SPLIT_PART_STAKE_PREFIX}{managed_id}",
        f"{_SPLIT_REMAINING_PREFIX}{managed_id}",
    )
    session.execute(
        delete(RuntimePreference).where(RuntimePreference.preference_key.in_(exact_keys))
    )


def _payload(row: RuntimePreference | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(str(row.preference_value or "{}"))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def install_vps_runtime_policy_hotfix(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    install_account_identity_canonical_authority()

    # Only a successful fresh Start resets financial-session recovery state.
    # Clear/Reset Trades remains a visibility/history action and never changes
    # recovery debt, Virtual Hook, Start/Stop, TP or SL state.
    @app.middleware("http")
    async def fresh_session_cleanup(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = str(request.url.path or "")
        method = str(request.method or "GET").upper()
        target = method == "POST" and path in {
            "/me/direct-execution/arm",
            "/api/me/direct-execution/arm",
        }
        account = _account(request) if target else None
        response = await call_next(request)
        if target and account and 200 <= int(getattr(response, "status_code", 500)) < 300:
            managed_id = int(account["id"])
            try:
                with base_api.DATABASE.session() as session:
                    _reset_recovery_state(session, managed_id)
            except Exception:
                base_api.LOGGER.exception(
                    "VPS_FRESH_SESSION_RECOVERY_CLEANUP_FAILED managed_id=%s",
                    managed_id,
                )
        return response

    _remove_route(app, "/me/direct-execution/status", "GET")

    @app.get("/me/direct-execution/status")
    def efficient_direct_execution_status(request: Request) -> JSONResponse:
        account = _account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        managed_id = int(account["id"])
        owner_key = f"{_OWNER_PREFIX}{managed_id}"
        stop_key = direct_hard_stop_key(managed_id)

        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id)
            if row is None:
                raise HTTPException(status_code=401, detail="Managed account was not found")
            preferences = {
                str(pref.preference_key): pref
                for pref in session.scalars(
                    select(RuntimePreference).where(
                        RuntimePreference.preference_key.in_((owner_key, stop_key))
                    )
                ).all()
            }
            stop = _payload(preferences.get(stop_key))
            hard_stop = bool(stop.get("active"))
            owner_payload = _payload(preferences.get(owner_key))
            remaining = direct_browser_lease_remaining_seconds(row)
            status = str(row.execution_status or "inactive").strip().lower()
            enabled = bool(row.enabled) and not hard_stop

        if hard_stop:
            owner = "stopped"
            status = "stopped"
            remaining = 0.0
        elif status == DIRECT_BROWSER_STATUS and remaining > 0 and enabled:
            owner = "browser"
        elif enabled:
            owner = "server_takeover" if status == DIRECT_BROWSER_STATUS else "server"
        else:
            owner = "stopped"

        return JSONResponse(
            {
                "authenticated": True,
                "owner": owner,
                "epoch": str(owner_payload.get("epoch") or ""),
                "execution_status": status,
                "enabled": enabled,
                "lease_remaining_seconds": round(float(remaining), 3),
                "hard_stop": hard_stop,
                "purchase_allowed": enabled,
            },
            headers={"Cache-Control": "no-store"},
        )

    app.state.vps_runtime_policy_hotfix_installed = True
    app.state.direct_status_query_policy = "one_account_read_one_batched_preference_read"
    app.state.reset_trades_financial_state_policy = "history_only"
    _INSTALLED = True
