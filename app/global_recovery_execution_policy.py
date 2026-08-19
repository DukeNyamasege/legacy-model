from __future__ import annotations

"""Final cross-account recovery and lifecycle policy.

This module is intentionally installed after every older recovery/lifecycle wrapper.
It makes the durable financial invariants explicit for both existing and future
Custom Strategy accounts:

* real recovery debt is sufficient to classify the next financial trade as recovery;
* Split N uses one fixed, cent-rounded stake for the N successful recovery legs;
* a new/larger loss pool starts a new equal-stake Split N cycle;
* the Split stake is never below the Deriv Options minimum stake of USD 0.50;
* historical 10%-of-balance recovery caps cannot stop/disable Auto Trading;
* only Take Profit, Stop Loss, or an explicit user Stop/Pause may stop execution.

A genuinely unaffordable recovery is a WAIT condition: the account remains enabled,
debt remains durable, and execution retries later. It is never a lifecycle stop.
"""

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app import custom_strategy_direct_runtime as direct_runtime
from app import custom_split_equal_spread_authority as equal_split
from app import manual_martingale_v2 as manual
from app import seamless_execution_recovery as seamless
from app.direct_execution_hard_stop_state import direct_hard_stop_active
from app.models import AccountRiskState, ManagedAccount, RuntimePreference, Trade, utc_now
from app.recovery import ceil_cents
from app.repositories.rf_dir5_repository import (
    REAL_RECOVERY_PENDING,
    VIRTUAL_WAITING_FOR_WIN,
    RFDir5Repository,
    StakePlan,
)
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot


LOGGER = logging.getLogger("deriv_bot")
DERIV_MINIMUM_STAKE = 0.50
PART_STAKE_PREFIX = "custom_equal_split_part_stake:"
SESSION_LIMIT_PREFIX = "session_risk_limits:v1:"

_INSTALLED = False
_ORIGINAL_PLAN_STAKE: Any = None
_ORIGINAL_RECORD_OUTCOME: Any = None
_ORIGINAL_FAIL_CLOSED: Any = None
_ORIGINAL_SET_STATUS: Any = None
_ORIGINAL_RUN: Any = None

_ALLOWED_TERMINAL = {"take_profit", "stop_loss"}
_MANUAL_STATUSES = {"stopped", "manual_pause"}
# These states are all automatic runtime/account failures. They may block one BUY
# until repaired, but they may never disable a user-started lifecycle. `stopped`
# and `manual_pause` are intentionally included here too: _terminal_allowed()
# consumes the genuine manual cases first, so any remaining write is synthetic.
_AUTOMATIC_TERMINAL = {
    "error",
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "contract_unavailable",
    "purchase_registration_error",
    "insufficient_balance",
    "purchase_insufficient_balance",
    "duplicate",
    "disabled",
    "inactive",
    "stopped",
    "manual_pause",
}
# Existing rows with one of these explicit automatic reasons can safely be restored
# on worker startup. Generic inactive/disabled/stopped are not included because an
# old explicit user Stop may predate the durable hard-stop sentinel.
_EXISTING_AUTOMATIC_STOP_STATUSES = {
    "error",
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "contract_unavailable",
    "purchase_registration_error",
    "insufficient_balance",
    "purchase_insufficient_balance",
}
_RECONNECT_MARKERS = (
    "connect",
    "socket",
    "session",
    "credential",
    "token",
    "transport",
    "timeout",
    "network",
    "runtime",
)


def _part_stake_key(managed_account_id: int) -> str:
    return f"{PART_STAKE_PREFIX}{int(managed_account_id)}"


def _read_part_stake(repository: RFDir5Repository, managed_account_id: int) -> float:
    base = manual._base_repository(repository)
    try:
        value = float(str(base.runtime_preference(_part_stake_key(managed_account_id)) or "0"))
    except (TypeError, ValueError, AttributeError):
        value = 0.0
    return max(0.0, value)


def _write_part_stake(
    repository: RFDir5Repository,
    managed_account_id: int,
    value: float,
) -> None:
    base = manual._base_repository(repository)
    try:
        base.set_runtime_preference(
            _part_stake_key(managed_account_id),
            f"{max(0.0, float(value or 0.0)):.8f}",
        )
    except Exception:
        pass


def equal_split_part_stake(
    *,
    recovery_basis_debt: float,
    proposal_profit_ratio: float,
    split_count: int,
    minimum_stake: float = DERIV_MINIMUM_STAKE,
) -> tuple[float, float, float]:
    """Return one fixed stake for every successful leg of a Split-N cycle.

    The first proposal of a new loss pool prices the entire recovery target once.
    That full recovery stake is then divided by configured N. The resulting part
    stake is persisted and reused, which keeps Split 2/3 legs equal apart from cent
    rounding. A small recovery buffer absorbs ordinary provider cent rounding.
    """

    debt = max(0.0, float(recovery_basis_debt or 0.0))
    ratio = max(0.0, float(proposal_profit_ratio or 0.0))
    parts = max(1, min(3, int(split_count or 1)))
    minimum = ceil_cents(max(DERIV_MINIMUM_STAKE, float(minimum_stake or 0.0)))
    if debt <= 0.009 or ratio <= 0:
        return minimum, minimum, 0.0

    buffer = max(0.05, debt * 0.06)
    target_profit = debt + buffer
    full_recovery_stake = ceil_cents(max(minimum, target_profit / ratio))
    part_stake = ceil_cents(max(minimum, full_recovery_stake / parts))
    return part_stake, full_recovery_stake, target_profit / parts


def _hard_stop(repository: Any, managed_id: int) -> bool:
    try:
        with repository.database.session() as session:
            return bool(direct_hard_stop_active(session, int(managed_id)))
    except Exception:
        return False


def _manual_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    markers = (
        "user stop",
        "user pressed",
        "manual stop",
        "manually stopped",
        "paused manually",
        "manual pause",
        "stopped manually",
        "start is required before execution",
        "auto trading stopped for this account mode",
    )
    return any(marker in text for marker in markers)


def _terminal_allowed(
    repository: Any,
    managed_id: int,
    status: str,
    reason: str,
) -> bool:
    normalized = str(status or "").strip().lower()
    if normalized in _ALLOWED_TERMINAL:
        return True
    if normalized in _MANUAL_STATUSES:
        return _hard_stop(repository, managed_id) or _manual_reason(reason)
    return False


def _waiting_status(reason: str) -> str:
    text = str(reason or "").strip().lower()
    return "reconnecting" if any(marker in text for marker in _RECONNECT_MARKERS) else "waiting_for_condition"


def _keep_account_running(repository: Any, managed_id: int, reason: str) -> None:
    if _hard_stop(repository, managed_id):
        return
    safe_reason = str(reason or "Automatic execution condition is being retried")[:160]
    with repository.database.session() as session:
        row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
        if row is None:
            return
        current = str(row.execution_status or "").strip().lower()
        if current in _ALLOWED_TERMINAL or (
            current in _MANUAL_STATUSES and _manual_reason(row.execution_status_reason or "")
        ):
            return
        row.enabled = True
        row.execution_status = _waiting_status(safe_reason)
        row.execution_status_reason = (
            f"Auto Trading remains active; retrying automatically. {safe_reason}"
        )[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()


def _force_recovery_state(repository: RFDir5Repository, managed_id: int) -> dict[str, Any]:
    with repository.database.session() as session:
        state = session.get(AccountRiskState, int(managed_id), with_for_update=True)
        if state is None:
            return {"debt": 0.0, "mode": ""}
        debt = max(0.0, float(state.recovery_loss_debt or 0.0))
        mode = str(state.protection_mode or "")
        if debt > 0.009:
            state.recovery_pending = True
            if mode != VIRTUAL_WAITING_FOR_WIN:
                state.protection_mode = REAL_RECOVERY_PENDING
                state.recovery_pending_since = state.recovery_pending_since or utc_now()
            state.updated_at = utc_now()
        return {"debt": debt, "mode": str(state.protection_mode or mode)}


def _split_settings(repository: RFDir5Repository, managed_id: int) -> dict[str, Any] | None:
    try:
        settings = manual.read_manual_martingale_settings(repository, int(managed_id))
        family = manual._manual_family(repository, int(managed_id))
    except Exception:
        return None
    if family == "system" or str(settings.get("mode") or "") != manual.SPLIT_MODE:
        return None
    return settings


def _plan_global_recovery(
    self: RFDir5Repository,
    *args: Any,
    **kwargs: Any,
) -> StakePlan:
    original = _ORIGINAL_PLAN_STAKE
    if original is None:
        raise RuntimeError("Recovery stake planner is unavailable")
    lower = original(self, *args, **kwargs)

    try:
        managed_id = int(kwargs.get("managed_account_id"))
    except (TypeError, ValueError):
        return lower
    settings = _split_settings(self, managed_id)
    if settings is None or _hard_stop(self, managed_id):
        return lower

    state = _force_recovery_state(self, managed_id)
    debt = float(state.get("debt") or 0.0)
    if debt <= 0.009:
        _write_part_stake(self, managed_id, 0.0)
        return lower
    if str(state.get("mode") or "") == VIRTUAL_WAITING_FOR_WIN:
        return lower

    ratio = max(0.0, float(kwargs.get("proposal_profit_ratio") or 0.0))
    if ratio <= 0:
        return StakePlan(
            None,
            "Split recovery is waiting for a valid Deriv proposal ratio; Auto Trading remains active",
            is_recovery=True,
            recovery_debt=debt,
            required_recovery_stake=0.0,
        )

    split_count = max(1, min(3, int(settings.get("split_count") or 1)))
    remaining = manual._read_split_remaining(self, managed_id)
    if remaining <= 0:
        remaining = split_count
        manual._write_split_remaining(self, managed_id, remaining)

    basis = equal_split._read_basis_debt(self, managed_id)
    if basis <= 0.009:
        basis = debt
        equal_split._write_basis_debt(self, managed_id, basis)

    fixed_stake = _read_part_stake(self, managed_id)
    full_recovery_stake = 0.0
    target_profit_per_leg = 0.0
    if fixed_stake < DERIV_MINIMUM_STAKE - 1e-9:
        fixed_stake, full_recovery_stake, target_profit_per_leg = equal_split_part_stake(
            recovery_basis_debt=basis,
            proposal_profit_ratio=ratio,
            split_count=split_count,
            minimum_stake=DERIV_MINIMUM_STAKE,
        )
        _write_part_stake(self, managed_id, fixed_stake)
    else:
        fixed_stake = ceil_cents(max(DERIV_MINIMUM_STAKE, fixed_stake))
        target_profit_per_leg = fixed_stake * ratio

    balance = max(0.0, float(kwargs.get("current_balance") or 0.0))
    if balance + 1e-9 < fixed_stake:
        _keep_account_running(
            self,
            managed_id,
            f"Recovery stake {fixed_stake:.2f} is waiting for sufficient account balance {balance:.2f}",
        )
        return StakePlan(
            None,
            (
                f"Split {split_count} recovery stake {fixed_stake:.2f} is temporarily "
                f"unaffordable at balance {balance:.2f}; Auto Trading remains active"
            ),
            is_recovery=True,
            recovery_debt=debt,
            required_recovery_stake=fixed_stake,
        )

    manual._mark_recovery_attempt(self, managed_id)
    _keep_account_running(self, managed_id, "Split recovery is ready for the next qualifying entry")
    LOGGER.warning(
        "GLOBAL_SPLIT_RECOVERY_STAKE managed_id=%s debt=%.2f basis_debt=%.2f "
        "split_count=%s remaining_successes=%s fixed_part_stake=%.2f "
        "proposal_profit_ratio=%.6f full_recovery_stake=%.2f "
        "target_profit_per_leg=%.2f recovery=true legacy_cap_ignored=true minimum_stake=%.2f",
        managed_id,
        debt,
        basis,
        split_count,
        remaining,
        fixed_stake,
        ratio,
        full_recovery_stake,
        target_profit_per_leg,
        DERIV_MINIMUM_STAKE,
    )
    return StakePlan(
        stake=fixed_stake,
        reason=(
            f"Split {split_count} fixed equal-stake recovery; {remaining} successful "
            f"part(s) remain; legacy percentage recovery cap is not a stop condition"
        ),
        is_recovery=True,
        recovery_debt=debt,
        required_recovery_stake=fixed_stake,
    )


def _record_global_recovery(
    self: RFDir5Repository,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    original = _ORIGINAL_RECORD_OUTCOME
    if original is None:
        raise RuntimeError("Recovery settlement recorder is unavailable")

    try:
        managed_id = int(kwargs.get("managed_account_id"))
        profit = float(kwargs.get("profit") or 0.0)
    except (TypeError, ValueError):
        return original(self, *args, **kwargs)
    settings = _split_settings(self, managed_id)
    result = original(self, *args, **kwargs)
    if settings is None:
        return result

    split_count = max(1, min(3, int(settings.get("split_count") or 1)))
    with self.database.session() as session:
        state = session.get(AccountRiskState, managed_id, with_for_update=True)
        debt = max(0.0, float(state.recovery_loss_debt or 0.0)) if state is not None else 0.0
        mode = str(state.protection_mode or "") if state is not None else ""
        if state is not None and debt > 0.009:
            state.recovery_pending = True
            if mode != VIRTUAL_WAITING_FOR_WIN:
                state.protection_mode = REAL_RECOVERY_PENDING
                state.recovery_pending_since = state.recovery_pending_since or utc_now()
            state.updated_at = utc_now()

    if debt <= 0.009:
        _write_part_stake(self, managed_id, 0.0)
        equal_split._clear_basis_debt(self, managed_id)
        manual._write_split_remaining(self, managed_id, 0)
        result.update(
            {
                "recovery_loss_debt": 0.0,
                "recovery_pending": False,
                "recovery_attempt_active": False,
            }
        )
        return result

    if profit < 0:
        equal_split._write_basis_debt(self, managed_id, debt)
        _write_part_stake(self, managed_id, 0.0)
        manual._write_split_remaining(self, managed_id, split_count)

    result.update(
        {
            "manual_martingale_mode": manual.SPLIT_MODE,
            "manual_split_total": split_count,
            "manual_split_remaining": manual._read_split_remaining(self, managed_id),
            "recovery_loss_debt": round(debt, 8),
            "recovery_pending": True,
            "recovery_classification": "REAL_DEBT_IS_RECOVERY",
        }
    )
    LOGGER.info(
        "GLOBAL_RECOVERY_CLASSIFICATION managed_id=%s debt=%.2f recovery=true "
        "protection_mode=%s profit=%.2f",
        managed_id,
        debt,
        mode,
        profit,
    )
    return result


def _fail_without_stopping(
    bot: RFDir5TradingBot,
    managed_id: int,
    reason: str,
    *,
    log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
) -> None:
    account = bot.repository.managed_account(int(managed_id)) or {}
    status = str(account.get("execution_status") or "").strip().lower()
    current_reason = str(account.get("execution_status_reason") or "")
    if _terminal_allowed(bot.repository, managed_id, status, current_reason):
        return

    _keep_account_running(bot.repository, int(managed_id), reason)
    try:
        seamless._schedule_runtime_repair(bot, int(managed_id))
    except Exception:
        pass
    bot.logger.warning(
        "%s managed_id=%s lifecycle_stop=false enabled_preserved=true auto_retry=true reason=%s",
        log_event,
        int(managed_id),
        str(reason or "runtime condition")[:160],
    )


def _status_without_automatic_stop(
    self: Test2Repository,
    account_id: int,
    execution_status: str,
    reason: str = "",
) -> None:
    original = _ORIGINAL_SET_STATUS
    if original is None:
        return

    managed_id = int(account_id)
    requested = str(execution_status or "inactive").strip().lower()
    if _terminal_allowed(self, managed_id, requested, reason):
        original(self, managed_id, requested, reason)
        return

    if requested not in _AUTOMATIC_TERMINAL:
        original(self, managed_id, requested, reason)
        return

    with self.database.session() as session:
        row = session.get(ManagedAccount, managed_id, with_for_update=True)
        risk = session.get(AccountRiskState, managed_id)
        if row is None:
            return
        debt = max(0.0, float(risk.recovery_loss_debt or 0.0)) if risk is not None else 0.0
        if not bool(row.enabled) and debt <= 0.009:
            # Do not restart an already-disabled ambiguous historical row here.
            # Explicit automatic legacy statuses are repaired once at worker boot;
            # old generic stopped/disabled/inactive rows remain user-controlled.
            row.execution_status_reason = str(reason or row.execution_status_reason or "")[:160]
            row.execution_status_updated_at = utc_now()
            return
        row.enabled = True
        row.execution_status = _waiting_status(reason)
        row.execution_status_reason = (
            f"Auto Trading remains active; automatic retry. {str(reason or requested)}"
        )[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()
    LOGGER.warning(
        "GLOBAL_AUTOMATIC_STOP_BLOCKED managed_id=%s requested_status=%s "
        "lifecycle_stop=false auto_retry=true",
        managed_id,
        requested,
    )


def _session_start(session: Any, managed_id: int) -> datetime | None:
    row = session.get(RuntimePreference, f"{SESSION_LIMIT_PREFIX}{managed_id}")
    if row is None:
        return None
    try:
        payload = json.loads(str(row.preference_value or "{}"))
        raw = str(payload.get("started_at") or "")
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def repair_current_session_trade_metrics(repository: Any) -> int:
    """Repair current-session Trade metrics that inherited global BotState P/L."""

    repaired = 0
    with repository.database.session() as session:
        account_ids = list(session.scalars(select(ManagedAccount.id)).all())
        for managed_id in account_ids:
            started_at = _session_start(session, int(managed_id))
            if started_at is None:
                continue
            rows = list(
                session.scalars(
                    select(Trade)
                    .where(
                        Trade.managed_account_id == int(managed_id),
                        Trade.settlement_time.is_not(None),
                        Trade.purchase_time >= started_at,
                    )
                    .order_by(Trade.settlement_time.asc(), Trade.id.asc())
                ).all()
            )
            cumulative = 0.0
            high_water = 0.0
            for trade in rows:
                cumulative = round(cumulative + float(trade.profit or 0.0), 8)
                high_water = max(high_water, cumulative)
                trade.cumulative_profit = round(cumulative, 8)
                trade.drawdown = round(max(0.0, high_water - cumulative), 8)
                repaired += 1
    return repaired


def _repair_existing_automatic_stops(repository: Any) -> int:
    """Restore explicitly automatic legacy stop states, never ambiguous user stops."""

    repaired = 0
    with repository.database.session() as session:
        rows = list(
            session.scalars(
                select(ManagedAccount).where(
                    ManagedAccount.enabled.is_(False),
                    ManagedAccount.execution_status.in_(sorted(_EXISTING_AUTOMATIC_STOP_STATUSES)),
                )
            ).all()
        )
        for account in rows:
            managed_id = int(account.id)
            if direct_hard_stop_active(session, managed_id):
                continue
            account.enabled = True
            account.execution_status = _waiting_status(account.execution_status_reason or account.execution_status)
            account.execution_status_reason = (
                "Existing automatic execution stop restored to retry under the global lifecycle policy."
            )[:160]
            account.execution_status_updated_at = utc_now()
            account.updated_at = utc_now()
            repaired += 1
    return repaired


def _repair_existing_recovery_accounts(repository: Any) -> int:
    repaired = 0
    with repository.database.session() as session:
        rows = list(
            session.execute(
                select(ManagedAccount, AccountRiskState)
                .join(AccountRiskState, AccountRiskState.managed_account_id == ManagedAccount.id)
                .where(AccountRiskState.recovery_loss_debt > 0.009)
            ).all()
        )
        for account, risk in rows:
            managed_id = int(account.id)
            if direct_hard_stop_active(session, managed_id):
                continue
            status = str(account.execution_status or "").strip().lower()
            reason = str(account.execution_status_reason or "")
            if status in _ALLOWED_TERMINAL or (
                status in _MANUAL_STATUSES and _manual_reason(reason)
            ):
                continue
            if status in _AUTOMATIC_TERMINAL or "recovery stake" in reason.lower():
                account.enabled = True
                account.execution_status = "recovery_pending"
                account.execution_status_reason = (
                    "Existing recovery debt restored under global never-auto-stop policy."
                )
                account.execution_status_updated_at = utc_now()
                account.updated_at = utc_now()
                repaired += 1
            risk.recovery_pending = True
            if str(risk.protection_mode or "") != VIRTUAL_WAITING_FOR_WIN:
                risk.protection_mode = REAL_RECOVERY_PENDING
                risk.recovery_pending_since = risk.recovery_pending_since or utc_now()
            risk.updated_at = utc_now()
    return repaired


async def _run_with_global_recovery_policy(self: RFDir5TradingBot) -> None:
    original = _ORIGINAL_RUN
    if original is None:
        return
    try:
        restored_auto = _repair_existing_automatic_stops(self.repository)
        restored_recovery = _repair_existing_recovery_accounts(self.repository)
        repaired_metrics = repair_current_session_trade_metrics(self.repository)
        self.logger.warning(
            "GLOBAL_RECOVERY_STARTUP_REPAIR automatic_stops=%s recovery_accounts=%s "
            "trade_metrics=%s stop_policy=tp_sl_or_manual_only",
            restored_auto,
            restored_recovery,
            repaired_metrics,
        )
    except Exception:
        self.logger.exception("GLOBAL_RECOVERY_STARTUP_REPAIR_FAILED")
    await original(self)


def install_global_recovery_execution_policy() -> None:
    """Install the final cross-account recovery and never-auto-stop authority."""

    global _INSTALLED
    global _ORIGINAL_PLAN_STAKE, _ORIGINAL_RECORD_OUTCOME, _ORIGINAL_FAIL_CLOSED
    global _ORIGINAL_SET_STATUS, _ORIGINAL_RUN
    if _INSTALLED:
        return

    _ORIGINAL_PLAN_STAKE = RFDir5Repository.plan_stake
    _ORIGINAL_RECORD_OUTCOME = RFDir5Repository.record_account_outcome
    _ORIGINAL_FAIL_CLOSED = direct_runtime._fail_closed
    _ORIGINAL_SET_STATUS = Test2Repository.set_managed_account_execution_status
    _ORIGINAL_RUN = RFDir5TradingBot.run

    RFDir5Repository.plan_stake = _plan_global_recovery  # type: ignore[method-assign]
    RFDir5Repository.record_account_outcome = _record_global_recovery  # type: ignore[method-assign]
    direct_runtime._fail_closed = _fail_without_stopping
    Test2Repository.set_managed_account_execution_status = _status_without_automatic_stop
    RFDir5TradingBot.run = _run_with_global_recovery_policy

    RFDir5TradingBot._global_recovery_execution_policy_installed = True
    RFDir5TradingBot._global_stop_policy = "take_profit_stop_loss_or_explicit_manual_stop_only"
    RFDir5TradingBot._global_split_policy = "fixed_equal_stake_per_configured_successful_leg_min_0_50"
    _INSTALLED = True
