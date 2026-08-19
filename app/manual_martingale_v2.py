from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import func, select

from app.models import AccountRiskState, ManagedAccount, Trade, VirtualTrade, utc_now
from app.recovery import ceil_cents
from app.repositories.rf_dir5_repository import (
    NORMAL_MODE,
    REAL_RECOVERY_PENDING,
    VIRTUAL_WAITING_FOR_WIN,
    RFDir5Repository,
    StakePlan,
)
from app.strategy_v2_preferences import read_strategy


VERSION = "manual-martingale-v2"
SYSTEM_MODE = "system"
MULTIPLIER_MODE = "multiplier"
SPLIT_MODE = "split"
ALLOWED_MODES = {SYSTEM_MODE, MULTIPLIER_MODE, SPLIT_MODE}
PREFERENCE_PREFIX = "manual_martingale_v2:"
SPLIT_REMAINING_PREFIX = "manual_martingale_v2_split_remaining:"
DEFAULT_MULTIPLIER = 2.0
DEFAULT_SPLIT_COUNT = 2
MAX_MULTIPLIER = 10.0

_STOPPED_STATUSES = {"stopped", "inactive", "disabled", "real_disabled"}
_PAUSED_STATUSES = {
    "manual_pause",
    "take_profit",
    "stop_loss",
    "insufficient_balance",
    "purchase_insufficient_balance",
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "contract_unavailable",
    "purchase_registration_error",
}

_WORKER_INSTALLED = False
_API_INSTALLED = False


def _base_repository(repository: Any) -> Any:
    return getattr(repository, "base", repository)


def _preference_key(managed_account_id: int) -> str:
    return f"{PREFERENCE_PREFIX}{int(managed_account_id)}"


def _split_key(managed_account_id: int) -> str:
    return f"{SPLIT_REMAINING_PREFIX}{int(managed_account_id)}"


def normalize_manual_martingale_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    mode = str(source.get("mode") or SYSTEM_MODE).strip().lower()
    if mode not in ALLOWED_MODES:
        mode = SYSTEM_MODE

    try:
        multiplier = float(source.get("multiplier", DEFAULT_MULTIPLIER))
    except (TypeError, ValueError):
        multiplier = DEFAULT_MULTIPLIER
    if not math.isfinite(multiplier):
        multiplier = DEFAULT_MULTIPLIER
    multiplier = round(max(1.10, min(MAX_MULTIPLIER, multiplier)), 2)

    try:
        split_count = int(source.get("split_count", DEFAULT_SPLIT_COUNT))
    except (TypeError, ValueError):
        split_count = DEFAULT_SPLIT_COUNT
    split_count = max(1, min(3, split_count))

    return {
        "mode": mode,
        "multiplier": multiplier,
        "split_count": split_count,
        "policy": (
            "system_exact_debt_recovery"
            if mode == SYSTEM_MODE
            else "user_multiplier"
            if mode == MULTIPLIER_MODE
            else "split_exact_debt_recovery"
        ),
        "version": VERSION,
    }


def read_manual_martingale_settings(repository: Any, managed_account_id: int) -> dict[str, Any]:
    base = _base_repository(repository)
    try:
        raw = str(base.runtime_preference(_preference_key(managed_account_id)) or "").strip()
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    return normalize_manual_martingale_settings(payload)


def save_manual_martingale_settings(
    repository: Any,
    managed_account_id: int,
    settings: dict[str, Any],
) -> dict[str, Any]:
    base = _base_repository(repository)
    normalized = normalize_manual_martingale_settings(settings)
    base.set_runtime_preference(
        _preference_key(managed_account_id),
        json.dumps(normalized, sort_keys=True, separators=(",", ":")),
    )
    return normalized


def _read_split_remaining(repository: Any, managed_account_id: int) -> int:
    base = _base_repository(repository)
    try:
        value = int(str(base.runtime_preference(_split_key(managed_account_id)) or "0"))
    except (TypeError, ValueError, AttributeError):
        value = 0
    return max(0, min(3, value))


def _write_split_remaining(repository: Any, managed_account_id: int, value: int) -> None:
    base = _base_repository(repository)
    try:
        base.set_runtime_preference(
            _split_key(managed_account_id),
            str(max(0, min(3, int(value)))),
        )
    except Exception:
        pass


def multiplier_recovery_stake(
    *,
    base_stake: float,
    consecutive_losses: int,
    multiplier: float,
) -> tuple[float, int]:
    """Classic multiplier sizing for a manual strategy recovery trade."""

    base = ceil_cents(max(0.35, float(base_stake or 0.0)))
    level = max(1, int(consecutive_losses or 0))
    factor = max(1.10, min(MAX_MULTIPLIER, float(multiplier or DEFAULT_MULTIPLIER)))
    return ceil_cents(base * (factor ** level)), level


def split_recovery_stake(
    *,
    base_stake: float,
    recovery_debt: float,
    proposal_profit_ratio: float,
    remaining_parts: int,
) -> tuple[float, float]:
    """Split the exact debt target across the remaining successful recovery parts.

    Each planned win targets an equal share of the loss pool. The live Deriv
    proposal ratio converts that target profit into the stake before purchase.
    """

    base = ceil_cents(max(0.35, float(base_stake or 0.0)))
    debt = max(0.0, float(recovery_debt or 0.0))
    ratio = float(proposal_profit_ratio or 0.0)
    if debt <= 0.009 or ratio <= 0:
        return base, base
    full_exact_stake = ceil_cents(max(base, debt / ratio))
    parts = max(1, min(3, int(remaining_parts or 1)))
    target_profit = debt / parts
    return ceil_cents(max(base, target_profit / ratio)), full_exact_stake


def _account_snapshot(repository: RFDir5Repository, managed_account_id: int) -> dict[str, Any]:
    with repository.database.session() as session:
        state = session.get(AccountRiskState, int(managed_account_id))
        row = session.get(ManagedAccount, int(managed_account_id))
        return {
            "enabled": bool(row.enabled) if row is not None else False,
            "status": str(row.execution_status or "inactive").strip().lower() if row is not None else "missing",
            "debt": float(state.recovery_loss_debt or 0.0) if state is not None else 0.0,
            "pending": bool(state.recovery_pending) if state is not None else False,
            "attempt_active": bool(state.recovery_attempt_active) if state is not None else False,
            "consecutive_losses": int(state.consecutive_losses or 0) if state is not None else 0,
            "mode": str(state.protection_mode or NORMAL_MODE) if state is not None else NORMAL_MODE,
        }


def _account_running(snapshot: dict[str, Any]) -> bool:
    status = str(snapshot.get("status") or "inactive").lower()
    return bool(snapshot.get("enabled")) and status not in _STOPPED_STATUSES | _PAUSED_STATUSES


def _manual_family(repository: RFDir5Repository, managed_account_id: int) -> str:
    try:
        return str(read_strategy(repository.database, int(managed_account_id)).family)
    except Exception:
        return "system"


def _is_recovery_snapshot(snapshot: dict[str, Any]) -> bool:
    return bool(
        float(snapshot.get("debt") or 0.0) > 0.009
        and (
            bool(snapshot.get("pending"))
            or bool(snapshot.get("attempt_active"))
            or str(snapshot.get("mode") or "") == REAL_RECOVERY_PENDING
        )
    )


def _safety_cap(
    *,
    current_balance: float,
    base_stake: float,
    maximum_recovery_balance_fraction: float,
    minimum_balance_reserve: float,
) -> float:
    balance = max(0.0, float(current_balance or 0.0))
    base = ceil_cents(max(0.35, float(base_stake or 0.0)))
    spendable = max(0.0, balance - float(minimum_balance_reserve or 0.0))
    fraction_cap = max(base, balance * max(0.0, float(maximum_recovery_balance_fraction or 0.0)))
    return max(0.0, min(spendable, fraction_cap))


def _mark_recovery_attempt(repository: RFDir5Repository, managed_account_id: int) -> None:
    with repository.database.session() as session:
        state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
        if state is None or state.protection_mode == VIRTUAL_WAITING_FOR_WIN:
            return
        state.recovery_pending = True
        state.recovery_attempt_active = True
        state.protection_mode = REAL_RECOVERY_PENDING
        state.recovery_pending_since = state.recovery_pending_since or utc_now()
        state.updated_at = utc_now()


def _reset_system_recovery_markers(repository: RFDir5Repository, managed_account_id: int) -> None:
    try:
        from app.ai_digit_recovery_v1 import _clear_split_remaining as clear_system_split

        clear_system_split(_base_repository(repository), int(managed_account_id))
    except Exception:
        pass
    try:
        from app.aidr_adaptive_virtual import reset_adaptive_trap

        reset_adaptive_trap(_base_repository(repository), int(managed_account_id))
    except Exception:
        pass


def _set_account_status(
    repository: RFDir5Repository,
    managed_account_id: int,
    status: str,
    reason: str,
) -> None:
    try:
        _base_repository(repository).set_managed_account_execution_status(
            int(managed_account_id),
            str(status),
            str(reason)[:160],
        )
    except Exception:
        pass


def _reset_manual_cycle(repository: RFDir5Repository, managed_account_id: int) -> None:
    with repository.database.session() as session:
        state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
        if state is None:
            return
        state.recovery_loss_debt = 0.0
        state.recovery_pending = False
        state.recovery_attempt_active = False
        state.consecutive_losses = 0
        state.protection_mode = NORMAL_MODE
        state.entered_virtual_mode_at = None
        state.recovery_pending_since = None
        state.virtual_observation_count = 0
        state.virtual_win_count = 0
        state.virtual_loss_count = 0
        state.current_virtual_loss_streak = 0
        state.updated_at = utc_now()
    _write_split_remaining(repository, managed_account_id, 0)
    _reset_system_recovery_markers(repository, managed_account_id)
    _set_account_status(
        repository,
        managed_account_id,
        "active",
        "Manual Martingale cycle completed. Next qualifying trade uses the base stake.",
    )


def _arm_next_split(
    repository: RFDir5Repository,
    managed_account_id: int,
    *,
    remaining_parts: int,
    cleanup: bool = False,
) -> None:
    with repository.database.session() as session:
        state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
        if state is None:
            return
        state.recovery_pending = True
        state.recovery_attempt_active = False
        state.consecutive_losses = 0
        state.protection_mode = REAL_RECOVERY_PENDING
        state.entered_virtual_mode_at = None
        state.virtual_observation_count = 0
        state.virtual_win_count = 0
        state.virtual_loss_count = 0
        state.current_virtual_loss_streak = 0
        state.recovery_pending_since = state.recovery_pending_since or utc_now()
        state.updated_at = utc_now()
    _write_split_remaining(repository, managed_account_id, remaining_parts)
    _reset_system_recovery_markers(repository, managed_account_id)
    _set_account_status(
        repository,
        managed_account_id,
        "recovery_pending",
        (
            "Split recovery provider repricing left residual debt; one cleanup part remains."
            if cleanup
            else f"Split Martingale recovery continues: {remaining_parts} successful part(s) remaining."
        ),
    )


def install_manual_martingale_v2_worker() -> None:
    """Install the final recovery stake policy for non-System strategy families.

    System Strategy delegates untouched to the strict AIDR planner. Manual
    strategies may keep that planner, replace only the recovery stake with a user
    multiplier, or divide the exact debt target across one to three successful
    recovery parts. Existing virtual-protection and account-lifecycle guards remain
    authoritative around every mode.
    """

    global _WORKER_INSTALLED
    if _WORKER_INSTALLED:
        return

    original_plan_stake = RFDir5Repository.plan_stake
    original_record_outcome = RFDir5Repository.record_account_outcome

    def final_manual_plan_stake(
        self: RFDir5Repository,
        *,
        managed_account_id: int,
        account_id_masked: str = "",
        current_balance: float,
        requested_stake: float,
        proposal_profit_ratio: float,
        recovery_enabled: bool,
        recovery_trigger_losses: int,
        minimum_stake: float,
        virtual_protection_enabled: bool = True,
        maximum_recovery_balance_fraction: float = 0.10,
        minimum_balance_reserve: float = 0.50,
    ) -> StakePlan:
        managed_id = int(managed_account_id)
        family = _manual_family(self, managed_id)
        settings = read_manual_martingale_settings(self, managed_id)
        before = _account_snapshot(self, managed_id)

        plan = original_plan_stake(
            self,
            managed_account_id=managed_id,
            account_id_masked=account_id_masked,
            current_balance=current_balance,
            requested_stake=requested_stake,
            proposal_profit_ratio=proposal_profit_ratio,
            recovery_enabled=recovery_enabled,
            recovery_trigger_losses=recovery_trigger_losses,
            minimum_stake=minimum_stake,
            virtual_protection_enabled=virtual_protection_enabled,
            maximum_recovery_balance_fraction=maximum_recovery_balance_fraction,
            minimum_balance_reserve=minimum_balance_reserve,
        )

        # The System Strategy is deliberately untouchable here.
        if family == "system" or str(settings["mode"]) == SYSTEM_MODE:
            return plan
        if not _account_running(before) or not _is_recovery_snapshot(before):
            return plan
        if before.get("attempt_active") or before.get("mode") == VIRTUAL_WAITING_FOR_WIN:
            return plan

        # The strict guard can move a second-loss account into virtual mode while
        # planning. Never override that zero-risk protection transition.
        after = _account_snapshot(self, managed_id)
        if not _account_running(after) or after.get("mode") == VIRTUAL_WAITING_FOR_WIN:
            return plan
        if float(proposal_profit_ratio or 0.0) <= 0:
            return plan

        base = ceil_cents(max(0.35, float(minimum_stake), float(requested_stake)))
        cap = _safety_cap(
            current_balance=current_balance,
            base_stake=base,
            maximum_recovery_balance_fraction=maximum_recovery_balance_fraction,
            minimum_balance_reserve=minimum_balance_reserve,
        )
        if base > cap + 1e-9:
            return plan

        mode = str(settings["mode"])
        if mode == MULTIPLIER_MODE:
            stake, level = multiplier_recovery_stake(
                base_stake=base,
                consecutive_losses=int(before.get("consecutive_losses") or 0),
                multiplier=float(settings["multiplier"]),
            )
            reason = (
                f"manual multiplier recovery x{float(settings['multiplier']):.2f} "
                f"level {level}"
            )
        else:
            remaining = _read_split_remaining(self, managed_id)
            if remaining <= 0:
                remaining = int(settings["split_count"])
                _write_split_remaining(self, managed_id, remaining)
            stake, full_exact = split_recovery_stake(
                base_stake=base,
                recovery_debt=float(before.get("debt") or 0.0),
                proposal_profit_ratio=float(proposal_profit_ratio),
                remaining_parts=remaining,
            )
            reason = (
                f"split System Martingale part {int(settings['split_count']) - remaining + 1}/"
                f"{int(settings['split_count'])}; exact full stake {full_exact:.2f} divided "
                f"across {remaining} remaining part(s)"
            )

        if stake > cap + 1e-9:
            return StakePlan(
                None,
                (
                    f"{mode} recovery stake {stake:.2f} exceeds account safety cap "
                    f"{cap:.2f}; debt retained"
                ),
                is_recovery=True,
                recovery_debt=float(before.get("debt") or 0.0),
                required_recovery_stake=stake,
            )

        # The underlying exact planner may have rejected its larger one-shot stake.
        # Once the user-selected lower-risk stake passes the same balance cap, arm
        # the exact same recovery lifecycle for this financial purchase.
        _mark_recovery_attempt(self, managed_id)
        return StakePlan(
            stake=stake,
            reason=reason,
            is_recovery=True,
            recovery_debt=float(before.get("debt") or 0.0),
            required_recovery_stake=stake,
        )

    def final_manual_record_outcome(self: RFDir5Repository, **kwargs: Any) -> dict[str, Any]:
        managed_id = int(kwargs.get("managed_account_id"))
        profit = float(kwargs.get("profit") or 0.0)
        family = _manual_family(self, managed_id)
        settings = read_manual_martingale_settings(self, managed_id)
        before = _account_snapshot(self, managed_id)
        was_recovery = _is_recovery_snapshot(before)

        result = original_record_outcome(self, **kwargs)

        if family == "system" or str(settings["mode"]) == SYSTEM_MODE:
            return result
        if bool(result.get("ignored_after_stop")):
            _write_split_remaining(self, managed_id, 0)
            return result

        mode = str(settings["mode"])
        if mode == MULTIPLIER_MODE:
            if profit > 0 and was_recovery:
                # Multiplier mode is intentionally user-defined rather than an
                # exact-debt promise. A winning multiplier recovery ends that
                # Martingale cycle and the next trade returns to base stake.
                _reset_manual_cycle(self, managed_id)
                result.update(
                    {
                        "manual_martingale_mode": MULTIPLIER_MODE,
                        "manual_multiplier": float(settings["multiplier"]),
                        "recovery_loss_debt": 0.0,
                        "recovery_pending": False,
                        "recovery_attempt_active": False,
                        "protection_mode": NORMAL_MODE,
                        "raw_protection_state": NORMAL_MODE,
                    }
                )
            return result

        # Split System Martingale: a normal loss opens N successful recovery
        # parts. A failed recovery does not consume a part; strict virtual
        # protection remains in charge until the next financial attempt.
        if mode == SPLIT_MODE and profit <= 0 and not was_recovery:
            after = _account_snapshot(self, managed_id)
            if float(after.get("debt") or 0.0) > 0.009 and bool(after.get("pending")):
                remaining = int(settings["split_count"])
                _write_split_remaining(self, managed_id, remaining)
                result.update(
                    {
                        "manual_martingale_mode": SPLIT_MODE,
                        "manual_split_total": int(settings["split_count"]),
                        "manual_split_remaining": remaining,
                    }
                )
            return result

        if mode == SPLIT_MODE and profit > 0 and was_recovery:
            remaining_before = _read_split_remaining(self, managed_id)
            if remaining_before <= 0:
                remaining_before = int(settings["split_count"])
            after = _account_snapshot(self, managed_id)
            debt_after = round(float(after.get("debt") or 0.0), 2)

            if debt_after <= 0.009:
                _reset_manual_cycle(self, managed_id)
                result.update(
                    {
                        "manual_martingale_mode": SPLIT_MODE,
                        "manual_split_total": int(settings["split_count"]),
                        "manual_split_remaining": 0,
                        "recovery_loss_debt": 0.0,
                        "recovery_pending": False,
                        "recovery_attempt_active": False,
                        "protection_mode": NORMAL_MODE,
                        "raw_protection_state": NORMAL_MODE,
                    }
                )
                return result

            remaining_after = max(0, remaining_before - 1)
            cleanup = False
            if remaining_after <= 0:
                # A requested Split-N cycle has exactly N successful parts. Do not
                # create a hidden extra recovery trade after its final success.
                _reset_manual_cycle(self, managed_id)
                result.update(
                    {
                        "manual_martingale_mode": SPLIT_MODE,
                        "manual_split_total": int(settings["split_count"]),
                        "manual_split_remaining": 0,
                        "manual_split_cleanup": False,
                        "manual_split_residual_unrecovered": debt_after,
                        "recovery_loss_debt": 0.0,
                        "recovery_pending": False,
                        "recovery_attempt_active": False,
                        "protection_mode": NORMAL_MODE,
                        "raw_protection_state": NORMAL_MODE,
                    }
                )
                return result
            _arm_next_split(
                self,
                managed_id,
                remaining_parts=remaining_after,
                cleanup=cleanup,
            )
            result.update(
                {
                    "manual_martingale_mode": SPLIT_MODE,
                    "manual_split_total": int(settings["split_count"]),
                    "manual_split_remaining": remaining_after,
                    "manual_split_cleanup": cleanup,
                    "recovery_loss_debt": debt_after,
                    "recovery_pending": True,
                    "recovery_attempt_active": False,
                    "protection_mode": "RECOVERY_PENDING",
                    "raw_protection_state": REAL_RECOVERY_PENDING,
                }
            )
        return result

    RFDir5Repository.plan_stake = final_manual_plan_stake
    RFDir5Repository.record_account_outcome = final_manual_record_outcome
    RFDir5Repository._manual_martingale_v2_worker_installed = True
    _WORKER_INSTALLED = True


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


def install_manual_martingale_v2_api(app: Any) -> None:
    """Expose account-scoped manual recovery choices on the Strategy page."""

    global _API_INSTALLED
    if _API_INSTALLED:
        return

    import app.api as base_api
    from fastapi import HTTPException, Request
    from pydantic import BaseModel, Field

    class ManualMartingaleRequest(BaseModel):
        mode: str = Field(pattern="^(system|multiplier|split)$")
        multiplier: float = Field(default=DEFAULT_MULTIPLIER, ge=1.10, le=MAX_MULTIPLIER)
        split_count: int = Field(default=DEFAULT_SPLIT_COUNT, ge=1, le=3)

    for path, method in (
        ("/me/manual-martingale", "GET"),
        ("/me/manual-martingale", "POST"),
    ):
        _remove_route(app, path, method)

    def current_payload(request: Request) -> tuple[dict[str, Any], Any, dict[str, Any], dict[str, Any]]:
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

    @app.get("/me/manual-martingale")
    def personal_manual_martingale(request: Request) -> dict[str, Any]:
        account, selection, settings, progress = current_payload(request)
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
        body: ManualMartingaleRequest,
    ) -> dict[str, Any]:
        account, selection, _current, progress = current_payload(request)
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
            "message": (
                "Manual strategy Martingale saved. Press Start when you are ready."
            ),
        }

    app.state.manual_martingale_v2_api_installed = True
    _API_INSTALLED = True
