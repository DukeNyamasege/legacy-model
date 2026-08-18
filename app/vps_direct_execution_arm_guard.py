from __future__ import annotations

"""Atomic browser ownership acquisition for direct execution.

The original direct-execution API owns the thin OTP/heartbeat/stop control plane.
This module replaces only /me/direct-execution/arm so two browsers/devices cannot
both acquire a fresh live financial lease for the same account.
"""

from typing import Any

from fastapi import HTTPException, Request

import app.api as base_api
from app.custom_strategy_api import _write_custom_martingale
from app.custom_strategy_v1 import read_custom_strategy, write_custom_strategy
from app.direct_execution_lease import (
    DIRECT_BROWSER_HEARTBEAT_SECONDS,
    DIRECT_BROWSER_LEASE_SECONDS,
    DIRECT_BROWSER_STATUS,
    direct_browser_lease_fresh,
)
from app.models import RuntimePreference, utc_now
from app.strategy_v2_preferences import write_strategy
from app.vps_direct_execution_api import (
    DirectArmRequest,
    _apply_execution_settings,
    _current_account,
    _key,
    _managed_row,
    _preference_payload,
    _write_owner_preference,
)
from app.vps_fast_execution_controls import (
    _delete_runtime_preferences_bounded,
    _reset_risk_state_bounded,
)

_INSTALLED = False


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


def install_vps_direct_execution_arm_guard(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/direct-execution/arm", "POST")

    @app.post("/me/direct-execution/arm")
    def atomic_arm_direct_execution(request: Request, body: DirectArmRequest) -> dict[str, Any]:
        account = _current_account(request)
        managed_id = int(account["id"])
        now = utc_now()

        with base_api.DATABASE.session() as session:
            # The account row lock makes the read-then-claim operation atomic. A
            # second phone cannot pass this check until the first claim commits.
            row = _managed_row(session, managed_id, for_update=True)
            owner_row = session.get(RuntimePreference, _key(managed_id))
            owner = _preference_payload(owner_row)
            existing_epoch = str(owner.get("epoch") or "")
            if (
                direct_browser_lease_fresh(row)
                and existing_epoch
                and existing_epoch != body.epoch
            ):
                raise HTTPException(
                    status_code=409,
                    detail="This account already has an active live-trading browser.",
                )

            strategy = dict(body.strategy or {})
            if strategy:
                # Keep the exact browser strategy ready for the worker if this
                # browser disappears. All writes are account-scoped and bounded.
                _reset_risk_state_bounded(session, managed_id)
                _delete_runtime_preferences_bounded(session, managed_id)
                config = write_custom_strategy(session, managed_id, strategy)
                write_strategy(
                    session,
                    managed_id,
                    family="custom",
                    side="custom",
                    prediction=None,
                )
                martingale = strategy.get("martingale")
                if isinstance(martingale, dict):
                    _write_custom_martingale(session, managed_id, martingale)
                _apply_execution_settings(row, strategy)
            else:
                config = read_custom_strategy(base_api.DATABASE, managed_id)
                if not bool(config.get("configured")):
                    raise HTTPException(
                        status_code=409,
                        detail="Save a strategy before starting direct execution",
                    )

            row.enabled = True
            row.execution_status = DIRECT_BROWSER_STATUS
            row.execution_status_reason = (
                "Browser owns live Deriv execution; VPS takeover waits for lease expiry"
            )[:160]
            row.execution_status_updated_at = now
            row.updated_at = now
            _write_owner_preference(
                session,
                managed_id,
                {
                    "epoch": body.epoch,
                    "owner": "browser",
                    "armed_at": now.isoformat(),
                    "last_heartbeat_at": now.isoformat(),
                    "heartbeat_seconds": DIRECT_BROWSER_HEARTBEAT_SECONDS,
                    "lease_seconds": DIRECT_BROWSER_LEASE_SECONDS,
                },
            )

        try:
            base_api.mark_dashboard_dirty(account.get("account_type"))
        except Exception:
            pass
        return {
            "success": True,
            "owner": "browser",
            "epoch": body.epoch,
            "lease_seconds": DIRECT_BROWSER_LEASE_SECONDS,
            "heartbeat_seconds": DIRECT_BROWSER_HEARTBEAT_SECONDS,
            "offline_takeover": True,
            "exclusive_owner": True,
        }

    app.state.vps_direct_execution_atomic_arm_installed = True
    _INSTALLED = True
