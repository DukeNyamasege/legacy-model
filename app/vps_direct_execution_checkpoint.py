from __future__ import annotations

"""Persist a tiny browser runtime checkpoint for safe offline worker takeover."""

import json
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

import app.api as base_api
from app import custom_split_equal_spread_authority as equal_split
from app import manual_martingale_v2 as manual
from app.direct_execution_lease import DIRECT_BROWSER_STATUS, direct_browser_lease_fresh
from app.models import AccountRiskState, RuntimePreference, utc_now
from app.vps_direct_execution_api import _current_account, _key, _managed_row, _preference_payload

_INSTALLED = False
CHECKPOINT_PREFIX = "direct_execution:checkpoint:v1:"
SPLIT_PART_STAKE_PREFIX = "custom_equal_split_part_stake:"


class DirectCheckpointRequest(BaseModel):
    epoch: str = Field(min_length=8, max_length=96)
    runtime: dict[str, Any] = Field(default_factory=dict)


def checkpoint_key(managed_id: int) -> str:
    return f"{CHECKPOINT_PREFIX}{int(managed_id)}"


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


def _write_checkpoint(session: Any, managed_id: int, payload: dict[str, Any]) -> None:
    key = checkpoint_key(managed_id)
    value = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=value))
    else:
        row.preference_value = value
        row.updated_at = utc_now()


def _write_split_part_stake(managed_id: int, value: float) -> None:
    base = manual._base_repository(base_api.REPOSITORY)
    try:
        base.set_runtime_preference(
            f"{SPLIT_PART_STAKE_PREFIX}{int(managed_id)}",
            f"{max(0.0, float(value or 0.0)):.8f}",
        )
    except Exception:
        pass


def _persist_split_handoff(
    managed_id: int,
    *,
    debt: float,
    split_basis_debt: float,
    split_remaining_wins: int,
    split_part_stake: float,
) -> None:
    """Keep browser Split-N progress identical when the VPS takes ownership."""

    if debt <= 0.009 or split_basis_debt <= 0.009 or split_remaining_wins <= 0:
        manual._write_split_remaining(base_api.REPOSITORY, managed_id, 0)
        equal_split._clear_basis_debt(base_api.REPOSITORY, managed_id)
        _write_split_part_stake(managed_id, 0.0)
        return
    equal_split._write_basis_debt(
        base_api.REPOSITORY,
        managed_id,
        split_basis_debt,
    )
    manual._write_split_remaining(
        base_api.REPOSITORY,
        managed_id,
        split_remaining_wins,
    )
    _write_split_part_stake(managed_id, split_part_stake)


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
            # Checkpoint does not mutate ManagedAccount. Do not take the row-level
            # FOR UPDATE lock used by the heartbeat, otherwise a slow checkpoint
            # can starve the very heartbeat that owns the browser execution lease.
            row = _managed_row(session, managed_id)
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
            split_basis_debt = _bounded_float(runtime.get("split_basis_debt"))
            split_remaining_wins = _bounded_int(runtime.get("split_remaining_wins"), high=3)
            split_part_stake = _bounded_float(runtime.get("split_part_stake"), high=1_000_000.0)
            losses = _bounded_int(runtime.get("consecutive_losses"), high=1000)
            virtual_mode = bool(runtime.get("virtual_mode"))
            virtual_wins = _bounded_int(runtime.get("virtual_wins"), high=1000)
            virtual_losses = _bounded_int(runtime.get("virtual_losses"), high=1000)
            observations = _bounded_int(runtime.get("virtual_observations"), high=100000)
            open_contracts = _bounded_int(runtime.get("open_contracts"), high=100)

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

            # Keep a small non-financial handoff record. The worker uses this only
            # to avoid starting a new contract while a browser-owned contract may
            # still be settling after an abrupt disconnect.
            _write_checkpoint(
                session,
                managed_id,
                {
                    "epoch": body.epoch,
                    "checkpointed_at": now.isoformat(),
                    "open_contracts": open_contracts,
                    "open_contract_ids": [
                        str(value)[:100]
                        for value in list(runtime.get("open_contract_ids") or [])[:20]
                        if str(value or "").strip()
                    ],
                    "session_profit": float(risk.session_profit or 0.0),
                    "recovery_debt": debt,
                    "split_basis_debt": split_basis_debt,
                    "split_remaining_wins": split_remaining_wins,
                    "split_part_stake": split_part_stake,
                    "consecutive_losses": losses,
                    "virtual_mode": virtual_mode,
                    "virtual_wins": virtual_wins,
                },
            )

        # These preferences are deliberately outside the ManagedAccount row lock.
        # If the browser disappears after this response, the worker continues the
        # exact same equal Split basis, fixed stake and remaining-success count.
        _persist_split_handoff(
            managed_id,
            debt=debt,
            split_basis_debt=split_basis_debt,
            split_remaining_wins=split_remaining_wins,
            split_part_stake=split_part_stake,
        )

        return {
            "success": True,
            "epoch": body.epoch,
            "checkpointed": True,
            "recovery_debt": round(debt, 8),
            "split_basis_debt": round(split_basis_debt, 8),
            "split_remaining_wins": split_remaining_wins,
            "split_part_stake": round(split_part_stake, 8),
            "virtual_mode": virtual_mode,
            "open_contracts": open_contracts,
        }

    app.state.vps_direct_execution_checkpoint_installed = True
    app.state.vps_direct_execution_checkpoint_split_continuity = True
    app.state.vps_direct_execution_checkpoint_fixed_split_stake = True
    app.state.vps_direct_execution_checkpoint_heartbeat_lock_independent = True
    _INSTALLED = True
