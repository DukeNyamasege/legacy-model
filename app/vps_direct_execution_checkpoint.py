from __future__ import annotations

"""Persist a tiny browser runtime checkpoint for safe offline worker takeover."""

from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

import app.api as base_api
from app.direct_execution_lease import DIRECT_BROWSER_STATUS, direct_browser_lease_fresh
from app.models import AccountRiskState, RuntimePreference, utc_now
from app.vps_direct_execution_api import _current_account, _key, _managed_row, _preference_payload

_INSTALLED = False


class DirectCheckpointRequest(BaseModel):
    epoch: str = Field(min_length=8, max_length=96)
    runtime: dict[str, Any] = Field(default_factory=dict)


def _bounded_float(value: Any, *, low: float = 0.0, high: float = 1_000_000.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number != number or number in {float("inf"), float("-inf")}:
        return 0.0
    return max(low, min(high, number))


def _bounded_int(value: Any, *, low: int = 0, high: int = 100_000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return max(low, min(high, number))


def install_vps_direct_execution_checkpoint(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    @app.post("/me/direct-execution/checkpoint")
    def checkpoint_direct_execution(request: Request, body: DirectCheckpointRequest) -> dict[str, Any]:
        account = _current_account(request)
        managed_id = int(account["id"])
        now = utc_now()
        runtime = dict(body.runtime or {})

        with base_api.DATABASE.session() as session:
            row = _managed_row(session, managed_id, for_update=True)
            owner = _preference_payload(session.get(RuntimePreference, _key(managed_id)))
            if str(owner.get("epoch") or "") != body.epoch:
                raise HTTPException(status_code=409, detail="Direct execution ownership changed")
            if (
                str(row.execution_status or "").strip().lower() != DIRECT_BROWSER_STATUS
                or not direct_browser_lease_fresh(row)
            ):
                raise HTTPException(status_code=409, detail="Browser no longer owns execution")

            risk = session.get(AccountRiskState, managed_id, with_for_update=True)
            if risk is None:
                risk = AccountRiskState(
                    managed_account_id=managed_id,
                    account_id_masked=str(account.get("account_id_masked") or ""),
                )
                session.add(risk)

            debt = _bounded_float(runtime.get("recovery_debt"))
            losses = _bounded_int(runtime.get("consecutive_losses"), high=1000)
            virtual_mode = bool(runtime.get("virtual_mode"))
            virtual_wins = _bounded_int(runtime.get("virtual_wins"), high=1000)
            virtual_losses = _bounded_int(runtime.get("virtual_losses"), high=1000)
            observations = _bounded_int(runtime.get("virtual_observations"), high=100000)

            risk.session_profit = _bounded_float(
                runtime.get("session_profit"), low=-1_000_000.0, high=1_000_000.0
            )
            risk.consecutive_losses = losses
            risk.recovery_loss_debt = debt
            risk.recovery_pending = debt > 0.009
            risk.recovery_attempt_active = False
            risk.virtual_observation_count = observations
            risk.virtual_win_count = virtual_wins
            risk.virtual_loss_count = virtual_losses
            risk.current_virtual_loss_streak = 0
            if virtual_mode:
                risk.protection_mode = "VIRTUAL_WAITING_FOR_WIN"
                risk.entered_virtual_mode_at = risk.entered_virtual_mode_at or now
            elif debt > 0.009:
                risk.protection_mode = "REAL_RECOVERY_PENDING"
                risk.recovery_pending_since = risk.recovery_pending_since or now
            else:
                risk.protection_mode = "NORMAL_MODE"
                risk.entered_virtual_mode_at = None
                risk.recovery_pending_since = None
            risk.updated_at = now

        return {
            "success": True,
            "epoch": body.epoch,
            "checkpointed": True,
            "recovery_debt": round(debt, 8),
            "virtual_mode": virtual_mode,
        }

    app.state.vps_direct_execution_checkpoint_installed = True
    _INSTALLED = True
