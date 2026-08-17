from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import socket
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

import app.api as base_api
from app.automation_schedule_models import AutomationSchedule
from app.custom_strategy_api import _open_count, _write_custom_martingale
from app.custom_strategy_comparator_extension import install_custom_strategy_comparator_extension
from app.custom_strategy_last_digit_prediction import install_custom_strategy_last_digit_prediction
from app.custom_strategy_result_routing import normalize_result_routing, write_result_routing
from app.final_public_controls import _reset_risk_state, _set_stopped
from app.manual_martingale_v2 import normalize_manual_martingale_settings
from app.models import ManagedAccount, utc_now
from app.services import telegram_admin
from app.strategy_v2_preferences import write_strategy
from app.telegram_silence import telegram_notifications_suspended
from app.token_store import decrypt_auth_payload

# Use the same extended Custom Strategy grammar as the production builder/worker.
install_custom_strategy_comparator_extension()
install_custom_strategy_last_digit_prediction()
from app import custom_strategy_v1 as custom_v1  # noqa: E402


LOGGER = logging.getLogger("legacy_model.automation_scheduler_action5")
DEFAULT_TIMEZONE = "Africa/Nairobi"
VALID_OVERLAP_POLICIES = {"wait", "skip", "replace"}
DUE_STATUSES = {"scheduled", "waiting"}
TERMINAL_STATUSES = {"completed", "skipped", "cancelled", "failed"}
ACCOUNT_FAILURE_STATUSES = {
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "purchase_registration_error",
    "contract_unavailable",
    "real_disabled",
    "insufficient_balance",
    "purchase_insufficient_balance",
}
_INSTALLED = False


class CreateAutomationScheduleRequest(BaseModel):
    strategy_name: str = Field(min_length=1, max_length=120)
    strategy_source: str = Field(default="saved", max_length=40)
    strategy_snapshot: dict[str, Any]
    date: str = Field(min_length=10, max_length=10)
    time: str = Field(min_length=5, max_length=8)
    timezone: str = Field(default=DEFAULT_TIMEZONE, min_length=1, max_length=80)
    stake: float = Field(ge=0.35, le=1_000_000.0)
    take_profit: float = Field(default=0.0, ge=0.0, le=1_000_000.0)
    stop_loss: float = Field(default=0.0, ge=0.0, le=1_000_000.0)
    overlap_policy: str = Field(default="wait", pattern="^(wait|skip|replace)$")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized is not None else None


def _finite_money(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return round(number, 2)


def local_schedule_to_utc(
    date_text: str,
    time_text: str,
    timezone_name: str,
) -> tuple[str, datetime]:
    """Validate a local IANA wall clock and convert it to authoritative UTC.

    DST gaps are rejected rather than silently shifted. An ambiguous fall-back
    hour deterministically uses fold=0 and the stored UTC instant remains stable.
    """

    try:
        zone = ZoneInfo(str(timezone_name or "").strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Choose a valid IANA timezone") from exc
    try:
        parsed_date = date.fromisoformat(str(date_text))
        parsed_time = time.fromisoformat(str(time_text))
    except ValueError as exc:
        raise ValueError("Choose a valid schedule date and time") from exc

    naive = datetime.combine(parsed_date, parsed_time.replace(tzinfo=None))
    local = naive.replace(tzinfo=zone, fold=0)
    converted = local.astimezone(timezone.utc)
    round_trip = converted.astimezone(zone).replace(tzinfo=None)
    if round_trip != naive:
        raise ValueError("That local time does not exist in the selected timezone")
    return local.isoformat(), converted


def _condition_from_last(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "digit_compare",
        "window": int(rule.get("window") or 1),
        "operator": str(rule.get("operator") or ">="),
        "value": int(rule.get("value") or 0),
    }


def _condition_from_percentage(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "percentage",
        "window": int(rule.get("window") or 100),
        "target": str(rule.get("target") or "even"),
        "operator": str(rule.get("operator") or ">="),
        "threshold": float(rule.get("threshold") or 0),
        "value": rule.get("value"),
    }


def _condition_from_direction(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "direction",
        "window": int(rule.get("window") or 1),
        "direction": str(rule.get("direction") or "rising"),
    }


def _builder_conditions(
    builder: dict[str, Any],
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    mode = str(
        builder.get("strategyMode") or builder.get("analysisMode") or "combined"
    ).strip().lower()
    last_rule = dict(builder.get("lastRule") or {})
    percentage_rule = dict(builder.get("percentageRule") or {})
    direction_rule = dict(builder.get("tickDirectionRule") or {})
    conditions: list[dict[str, Any]] = []
    if mode in {"last_digit", "combined"} and last_rule:
        conditions.append(_condition_from_last(last_rule))
    if mode in {"percentage", "combined"} and percentage_rule:
        conditions.append(_condition_from_percentage(percentage_rule))
    if bool(direction_rule.get("enabled")):
        conditions.append(_condition_from_direction(direction_rule))
    if not conditions:
        raise ValueError(f"{prefix}strategy requires at least one condition")
    return conditions


def _compile_builder(snapshot: dict[str, Any]) -> dict[str, Any]:
    builder = dict(snapshot.get("builder") or snapshot)
    market_mode = str(builder.get("marketMode") or "all").strip().lower()
    if market_mode in {"one", "single"}:
        market_mode = "single"
        source_markets = list(builder.get("markets") or ["1HZ100V"])
        markets = [str(builder.get("oneMarket") or source_markets[0])]
    elif market_mode == "selected":
        markets = list(builder.get("markets") or [])
    else:
        market_mode = "all"
        markets = []

    trade = dict(builder.get("trade") or {})
    side = str(trade.get("side") or "over").strip().lower()
    reanalyze = dict(builder.get("reanalyze") or {})
    prediction: Any = trade.get("prediction")
    prediction_mode = str(
        snapshot.get("predictionMode")
        or builder.get("predictionMode")
        or ""
    ).strip().lower()
    if side in {"matches", "differs"} and prediction_mode:
        prediction = None
        reanalyze["prediction_mode"] = prediction_mode
        if prediction_mode in {
            "most_appearing",
            "second_most_appearing",
            "least_appearing",
        }:
            reanalyze["prediction_window"] = int(
                snapshot.get("predictionWindow")
                or builder.get("predictionWindow")
                or 100
            )

    money = dict(builder.get("money") or {})
    virtual = dict(builder.get("virtualHook") or {})
    raw: dict[str, Any] = {
        "market_mode": market_mode,
        "markets": markets,
        "trade_type": side,
        "prediction": prediction,
        "duration_ticks": int(money.get("ticks") or 1),
        "conditions": _builder_conditions(builder),
        "match": "all",
        "reanalyze": reanalyze,
        "virtual_hook_enabled": bool(virtual.get("enabled", True)),
        "virtual_hook": {
            "enabled": bool(virtual.get("enabled", True)),
            "enter_after_losses": int(virtual.get("enterAfterLosses") or 2),
            "exit_after_consecutive_wins": int(
                virtual.get("exitAfterConsecutiveWins") or 2
            ),
        },
    }
    if prediction_mode:
        raw["prediction_mode"] = prediction_mode
        if "prediction_window" in reanalyze:
            raw["prediction_window"] = reanalyze["prediction_window"]
    return raw


def _compile_after_loss(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    result = dict(snapshot.get("result") or {})
    if not bool(result.get("routingEnabled")):
        return None
    source = dict(result.get("afterLoss") or {})
    if not source:
        raise ValueError(
            "Result-Based Trading is enabled but the After Loss strategy is missing"
        )
    proxy = {
        "analysisMode": source.get("analysisMode") or "last_digit",
        "lastRule": source.get("lastRule") or {},
        "percentageRule": source.get("percentageRule") or {},
        "tickDirectionRule": source.get("tickDirectionRule") or {},
    }
    return {
        "trade_type": str(source.get("tradeType") or "over"),
        "prediction": source.get("prediction"),
        "duration_ticks": int(source.get("durationTicks") or 1),
        "conditions": _builder_conditions(proxy, prefix="After-loss "),
        "match": "all",
    }


def canonical_strategy_snapshot(
    snapshot: dict[str, Any],
    *,
    stake: float,
    take_profit: float,
    stop_loss: float,
) -> dict[str, Any]:
    """Freeze a browser/library strategy into server-valid execution inputs."""

    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("Choose a strategy before scheduling")
    if len(json.dumps(snapshot, separators=(",", ":"), default=str)) > 120_000:
        raise ValueError("Strategy snapshot is too large")

    custom_raw: Any = snapshot.get("custom_strategy") or snapshot.get("config")
    if not isinstance(custom_raw, dict) or not custom_raw.get("conditions"):
        custom_raw = _compile_builder(snapshot)
    normalized_custom = custom_v1.normalize_custom_strategy(custom_raw)

    raw_martingale = snapshot.get("martingale")
    if not isinstance(raw_martingale, dict):
        builder = dict(snapshot.get("builder") or snapshot)
        money = dict(builder.get("money") or {})
        result = dict(snapshot.get("result") or {})
        raw_martingale = {
            "mode": str(result.get("recoveryMode") or "multiplier"),
            "multiplier": float(money.get("martingale") or 1.2),
            "split_count": int(result.get("splitCount") or 2),
        }
    martingale = normalize_manual_martingale_settings(raw_martingale)

    raw_routing = snapshot.get("result_routing")
    if isinstance(raw_routing, dict):
        routing = normalize_result_routing(raw_routing)
    else:
        after_loss = _compile_after_loss(snapshot)
        routing = normalize_result_routing(
            {"enabled": bool(after_loss), "after_loss": after_loss}
        )

    return {
        "version": "action5-schedule-snapshot-v1",
        "custom_strategy": normalized_custom,
        "martingale": martingale,
        "result_routing": routing,
        "execution_settings": {
            "stake_amount": _finite_money(stake, "Stake"),
            "take_profit": _finite_money(take_profit, "Take profit"),
            "stop_loss": _finite_money(stop_loss, "Stop loss"),
            "martingale_enabled": True,
        },
    }


def _account_token_ready(row: ManagedAccount) -> bool:
    status = str(row.execution_status or "inactive")
    reason = str(row.execution_status_reason or "")
    if base_api.execution_requires_new_token(status):
        return False
    if base_api.execution_token_was_rejected(status, reason):
        return False
    try:
        payload = decrypt_auth_payload(
            row.token_secret,
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception:
        return False
    return bool(base_api.has_personal_trading_api_token(payload))


def _account_label(row: ManagedAccount) -> str:
    try:
        payload = decrypt_auth_payload(
            row.token_secret,
            base_api.CONFIG.deriv.token_encryption_key,
        )
        account_id = str(payload.get("account_id") or "").strip()
        if account_id:
            return base_api.mask_account_id(account_id)
    except Exception:
        pass
    return str(row.label or f"Account {row.id}")


def _schedule_dict(row: AutomationSchedule) -> dict[str, Any]:
    return {
        "id": row.id,
        "managed_account_id": int(row.managed_account_id),
        "strategy_name": row.strategy_name,
        "strategy_source": row.strategy_source,
        "date_time_local": row.scheduled_local,
        "timezone": row.timezone,
        "scheduled_for_utc": _iso_utc(row.scheduled_for_utc),
        "stake": float(row.stake_amount),
        "take_profit": float(row.take_profit),
        "stop_loss": float(row.stop_loss),
        "overlap_policy": row.overlap_policy,
        "status": row.status,
        "status_reason": row.status_reason,
        "started_at": _iso_utc(row.started_at),
        "completed_at": _iso_utc(row.completed_at),
        "cancelled_at": _iso_utc(row.cancelled_at),
        "created_at": _iso_utc(row.created_at),
    }


def _queue_private_schedule_alert(
    schedule_id: str,
    event: str,
    reason: str = "",
) -> None:
    if telegram_notifications_suspended():
        return

    def work() -> None:
        with base_api.DATABASE.session() as session:
            schedule = session.get(AutomationSchedule, str(schedule_id))
            if schedule is None:
                return
            account = session.get(ManagedAccount, int(schedule.managed_account_id))
            if account is None:
                return
            label = _account_label(account)
            local = str(schedule.scheduled_local).replace("T", " ")
            event_name = str(event).strip().lower()
            titles = {
                "created": "🗓 SCHEDULED TRADING SESSION CREATED",
                "started": "▶️ SCHEDULED TRADING SESSION STARTED",
                "completed": "✅ SCHEDULED TRADING SESSION FINISHED",
                "skipped": "⏭ SCHEDULED TRADING SESSION SKIPPED",
                "cancelled": "🛑 SCHEDULED TRADING SESSION CANCELLED",
                "failed": "⚠️ SCHEDULED TRADING SESSION FAILED",
            }
            title = titles.get(event_name, titles["failed"])
            lines = [
                title,
                "",
                f"Account: {label}",
                f"Strategy: {schedule.strategy_name}",
                f"Scheduled: {local} ({schedule.timezone})",
                f"Stake: {float(schedule.stake_amount):.2f} USD",
                (
                    f"TP / SL: {float(schedule.take_profit):.2f} / "
                    f"{float(schedule.stop_loss):.2f} USD"
                ),
                f"Status: {str(schedule.status).upper()}",
            ]
            if reason or schedule.status_reason:
                lines.append(f"Reason: {reason or schedule.status_reason}")
            text = "\n".join(lines)
        telegram_admin._send_private_sync(
            base_api.REPOSITORY,
            base_api.CONFIG.telegram,
            LOGGER,
            text,
        )

    telegram_admin._queue(work)


def _mark_terminal(schedule_id: str, status: str, reason: str) -> bool:
    now = utc_now()
    changed = False
    with base_api.DATABASE.session() as session:
        row = session.get(
            AutomationSchedule,
            str(schedule_id),
            with_for_update=True,
        )
        if row is None or row.status in TERMINAL_STATUSES:
            return False
        row.status = status
        row.status_reason = str(reason or "")[:2000]
        row.completed_at = now
        row.claimed_by = ""
        row.claim_expires_at = None
        row.updated_at = now
        changed = True
    if changed:
        event = (
            "skipped"
            if status == "skipped"
            else "failed"
            if status == "failed"
            else "completed"
        )
        _queue_private_schedule_alert(schedule_id, event, reason)
    return changed


def _reconcile_running_schedules() -> None:
    now = utc_now()
    with base_api.DATABASE.session() as session:
        rows = list(
            session.scalars(
                select(AutomationSchedule)
                .where(AutomationSchedule.status.in_(["running", "starting"]))
                .order_by(AutomationSchedule.scheduled_for_utc)
            ).all()
        )
        items = [
            (row.id, _as_utc(row.claim_expires_at))
            for row in rows
        ]

    for schedule_id, claim_expires_at in items:
        alert: tuple[str, str] | None = None
        with base_api.DATABASE.session() as session:
            schedule = session.get(
                AutomationSchedule,
                schedule_id,
                with_for_update=True,
            )
            if schedule is None or schedule.status not in {"running", "starting"}:
                continue
            account = session.get(ManagedAccount, int(schedule.managed_account_id))
            if account is None:
                schedule.status = "failed"
                schedule.status_reason = "Managed account no longer exists"
                schedule.completed_at = now
                schedule.claimed_by = ""
                schedule.claim_expires_at = None
                schedule.updated_at = now
                alert = ("failed", schedule.status_reason)
            elif schedule.status == "starting":
                scheduler_marker = f"Scheduled session {schedule.id}"
                reason = str(account.execution_status_reason or "")
                if bool(account.enabled) and scheduler_marker in reason:
                    schedule.status = "running"
                    schedule.status_reason = "Scheduled session execution is active"
                    schedule.started_at = schedule.started_at or now
                    schedule.claimed_by = ""
                    schedule.claim_expires_at = None
                    schedule.updated_at = now
                    alert = ("started", "")
                elif bool(account.enabled):
                    # A manual/other session became active after the claim. Never
                    # mislabel it as this scheduled strategy; re-evaluate overlap.
                    schedule.status = "waiting"
                    schedule.status_reason = (
                        "Recovered scheduler claim while another trading session "
                        "was active; overlap policy will be re-evaluated"
                    )
                    schedule.claimed_by = ""
                    schedule.claim_expires_at = None
                    schedule.updated_at = now
                elif claim_expires_at and claim_expires_at <= now:
                    due = _as_utc(schedule.scheduled_for_utc) or now
                    grace = timedelta(
                        seconds=max(
                            60,
                            int(
                                os.getenv(
                                    "AUTOMATION_SCHEDULE_LATE_GRACE_SECONDS",
                                    "900",
                                )
                            ),
                        )
                    )
                    if now - due <= grace:
                        schedule.status = "scheduled"
                        schedule.status_reason = (
                            "Recovered an interrupted scheduler claim; retrying safely"
                        )
                        schedule.claimed_by = ""
                        schedule.claim_expires_at = None
                        schedule.updated_at = now
                    else:
                        schedule.status = "skipped"
                        schedule.status_reason = (
                            "Scheduled start was interrupted and exceeded the "
                            "late-start safety window"
                        )
                        schedule.completed_at = now
                        schedule.claimed_by = ""
                        schedule.claim_expires_at = None
                        schedule.updated_at = now
                        alert = ("skipped", schedule.status_reason)
            elif not bool(account.enabled):
                account_status = str(
                    account.execution_status or "stopped"
                ).strip().lower()
                schedule.status = (
                    "failed"
                    if account_status in ACCOUNT_FAILURE_STATUSES
                    else "completed"
                )
                schedule.status_reason = str(
                    account.execution_status_reason
                    or account_status
                    or "Trading stopped"
                )
                schedule.completed_at = now
                schedule.claimed_by = ""
                schedule.claim_expires_at = None
                schedule.updated_at = now
                alert = (
                    "failed" if schedule.status == "failed" else "completed",
                    schedule.status_reason,
                )

        if alert is not None:
            _queue_private_schedule_alert(schedule_id, alert[0], alert[1])


def _claim_next_due(worker_id: str) -> str | None:
    now = utc_now()
    skipped_id: str | None = None
    with base_api.DATABASE.session() as session:
        statement = (
            select(AutomationSchedule)
            .where(
                AutomationSchedule.status.in_(list(DUE_STATUSES)),
                AutomationSchedule.scheduled_for_utc <= now,
            )
            .order_by(
                AutomationSchedule.scheduled_for_utc,
                AutomationSchedule.created_at,
            )
            .limit(1)
        )
        if base_api.DATABASE.engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = session.scalar(statement)
        if row is None:
            return None

        due = _as_utc(row.scheduled_for_utc) or now
        grace = timedelta(
            seconds=max(
                60,
                int(os.getenv("AUTOMATION_SCHEDULE_LATE_GRACE_SECONDS", "900")),
            )
        )
        # Waiting is allowed to remain due indefinitely because the trader chose
        # "wait until previous session finishes". The late guard applies only to
        # a session that was never admitted to waiting.
        if row.status == "scheduled" and now - due > grace:
            row.status = "skipped"
            row.status_reason = (
                "Session was not started within the configured late-start safety window"
            )
            row.completed_at = now
            row.updated_at = now
            skipped_id = row.id
        else:
            row.status = "starting"
            row.status_reason = "Scheduler claimed this session for exactly-once startup"
            row.claimed_by = worker_id[:120]
            row.claim_expires_at = now + timedelta(seconds=45)
            row.updated_at = now
            return row.id

    if skipped_id is not None:
        _queue_private_schedule_alert(skipped_id, "skipped")
    return None


def _finish_replaced_running_schedules(
    session: Any,
    managed_account_id: int,
    replacing_id: str,
) -> list[str]:
    now = utc_now()
    rows = list(
        session.scalars(
            select(AutomationSchedule).where(
                AutomationSchedule.managed_account_id == int(managed_account_id),
                AutomationSchedule.status == "running",
                AutomationSchedule.id != str(replacing_id),
            )
        ).all()
    )
    completed: list[str] = []
    for row in rows:
        row.status = "completed"
        row.status_reason = f"Stopped by overlapping scheduled session {replacing_id}"
        row.completed_at = now
        row.updated_at = now
        completed.append(str(row.id))
    return completed


def _apply_schedule_strategy(schedule_id: str) -> tuple[bool, str]:
    """Apply a frozen strategy, then enter the existing Custom Runtime start state.

    This function never talks to Deriv directly. The existing account-scoped
    Custom Strategy worker remains the only proposal/purchase authority.
    """

    now = utc_now()
    outcome = ""
    replaced_ids: list[str] = []
    managed_id = 0
    strategy_name = ""

    with base_api.DATABASE.session() as session:
        schedule = session.get(
            AutomationSchedule,
            schedule_id,
            with_for_update=True,
        )
        if schedule is None or schedule.status != "starting":
            return False, "Schedule is no longer startable"

        account = session.get(
            ManagedAccount,
            int(schedule.managed_account_id),
            with_for_update=True,
        )
        if account is None:
            return False, "Managed account no longer exists"
        if not _account_token_ready(account):
            return (
                False,
                "A valid Deriv trade-scope credential is required at scheduled start",
            )

        policy = str(schedule.overlap_policy or "wait").lower()
        if policy not in VALID_OVERLAP_POLICIES:
            policy = "wait"
        account_active = bool(account.enabled)

        if account_active and policy == "wait":
            schedule.status = "waiting"
            schedule.status_reason = "Waiting until the current trading session finishes"
            schedule.claimed_by = ""
            schedule.claim_expires_at = None
            schedule.updated_at = now
            outcome = "waiting"
        elif account_active and policy == "skip":
            schedule.status = "skipped"
            schedule.status_reason = (
                "Skipped because another trading session was still active"
            )
            schedule.completed_at = now
            schedule.claimed_by = ""
            schedule.claim_expires_at = None
            schedule.updated_at = now
            outcome = "skipped"
        else:
            if account_active and policy == "replace":
                # Same transaction + same row lock: use the existing destructive
                # Stop semantics without opening a nested repository transaction.
                _set_stopped(session, account)
                account.execution_status_reason = (
                    f"Stopped by scheduled session {schedule.id} before replacement"
                )[:160]
                replaced_ids = _finish_replaced_running_schedules(
                    session,
                    int(account.id),
                    schedule.id,
                )

            open_count = _open_count(session, int(account.id))
            if open_count:
                schedule.status = "waiting"
                schedule.status_reason = (
                    f"Waiting for {open_count} open actual/virtual contract(s) "
                    "to settle before scheduled start"
                )
                schedule.claimed_by = ""
                schedule.claim_expires_at = None
                schedule.updated_at = now
                outcome = "waiting"
            else:
                frozen = dict(schedule.strategy_snapshot or {})
                custom = dict(frozen.get("custom_strategy") or {})
                settings = dict(frozen.get("execution_settings") or {})
                martingale = dict(frozen.get("martingale") or {})
                routing = dict(
                    frozen.get("result_routing") or {"enabled": False}
                )
                try:
                    custom_v1.write_custom_strategy(
                        session,
                        int(account.id),
                        custom,
                    )
                    _write_custom_martingale(
                        session,
                        int(account.id),
                        martingale,
                    )
                    write_result_routing(
                        session,
                        int(account.id),
                        routing,
                    )
                except ValueError as exc:
                    return (
                        False,
                        f"Saved strategy snapshot is no longer valid: {exc}",
                    )

                account.stake_amount = _finite_money(
                    settings.get("stake_amount", schedule.stake_amount),
                    "Stake",
                )
                account.take_profit = _finite_money(
                    settings.get("take_profit", schedule.take_profit),
                    "Take profit",
                )
                account.stop_loss = _finite_money(
                    abs(settings.get("stop_loss", schedule.stop_loss)),
                    "Stop loss",
                )
                account.martingale_enabled = bool(
                    settings.get("martingale_enabled", True)
                )
                _reset_risk_state(session, int(account.id))
                write_strategy(
                    session,
                    int(account.id),
                    family="custom",
                    side="custom",
                    prediction=None,
                )

                # Exact existing direct Custom Runtime transition. The financial
                # worker notices enabled+starting and owns all provider traffic.
                account.enabled = True
                account.execution_status = "starting"
                account.execution_status_reason = (
                    f"Scheduled session {schedule.id} initializing authenticated "
                    "account execution"
                )[:160]
                account.execution_status_updated_at = now
                account.updated_at = now

                schedule.status = "running"
                schedule.status_reason = "Scheduled session execution initialized"
                schedule.started_at = now
                schedule.claimed_by = ""
                schedule.claim_expires_at = None
                schedule.updated_at = now
                managed_id = int(account.id)
                strategy_name = schedule.strategy_name
                outcome = "running"

    # All notifications occur after the database transaction commits so the
    # private Telegram sender never tries to read rows locked by this scheduler.
    for replaced_id in replaced_ids:
        _queue_private_schedule_alert(
            replaced_id,
            "completed",
            f"Stopped by overlapping scheduled session {schedule_id}",
        )

    if outcome == "running":
        base_api.REPOSITORY.audit(
            "AUTOMATION_SCHEDULE_STARTED",
            "persistent_scheduler",
            socket.gethostname(),
            {
                "schedule_id": schedule_id,
                "managed_account_id": managed_id,
                "strategy_name": strategy_name,
                "execution_authority": "existing_custom_strategy_worker",
                "tp_sl_authority": "existing_session_risk_stop_authority",
            },
        )
        _queue_private_schedule_alert(schedule_id, "started")
        return True, "running"
    return False, outcome or "Schedule was not started"


def run_scheduler_cycle() -> dict[str, int]:
    """One deterministic server-side scheduler pass; safe after process restart."""

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    _reconcile_running_schedules()
    claimed = 0
    started = 0
    max_starts = max(
        1,
        int(os.getenv("AUTOMATION_SCHEDULE_MAX_STARTS_PER_CYCLE", "8")),
    )
    for _ in range(max_starts):
        schedule_id = _claim_next_due(worker_id)
        if not schedule_id:
            break
        claimed += 1
        try:
            ok, outcome = _apply_schedule_strategy(schedule_id)
            if ok:
                started += 1
            elif outcome == "skipped":
                _queue_private_schedule_alert(schedule_id, "skipped")
            elif outcome != "waiting":
                _mark_terminal(schedule_id, "failed", outcome)
        except Exception as exc:
            LOGGER.exception(
                "AUTOMATION_SCHEDULE_START_FAILED schedule_id=%s",
                schedule_id,
            )
            _mark_terminal(
                schedule_id,
                "failed",
                f"{type(exc).__name__}: {exc}",
            )
    return {"claimed": claimed, "started": started}


async def _scheduler_loop(stop_event: asyncio.Event) -> None:
    interval = max(
        0.5,
        float(os.getenv("AUTOMATION_SCHEDULER_INTERVAL_SECONDS", "1")),
    )
    LOGGER.warning(
        "AUTOMATION_SCHEDULER_ACTION5_ACTIVE interval_seconds=%.2f "
        "persistence=database exactly_once=claim_state overlap=wait_skip_replace "
        "timezone=iana_to_utc purchase_authority=existing_custom_worker",
        interval,
    )
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run_scheduler_cycle)
        except Exception:
            LOGGER.exception("AUTOMATION_SCHEDULER_CYCLE_FAILED")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def _current_account(request: Request) -> dict[str, Any]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Log in with Deriv first")
    if account.get("local_dev_preview"):
        raise HTTPException(
            status_code=409,
            detail="Scheduling is unavailable in local preview mode",
        )
    return account


def install_automation_scheduler_action5(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    @app.get("/me/automation-schedules")
    def list_automation_schedules(
        request: Request,
        limit: int = 50,
    ) -> dict[str, Any]:
        account = _current_account(request)
        safe_limit = max(1, min(200, int(limit)))
        with base_api.DATABASE.session() as session:
            rows = list(
                session.scalars(
                    select(AutomationSchedule)
                    .where(
                        AutomationSchedule.managed_account_id
                        == int(account["id"])
                    )
                    .order_by(AutomationSchedule.scheduled_for_utc.desc())
                    .limit(safe_limit)
                ).all()
            )
        items = [_schedule_dict(row) for row in rows]
        return {
            "authenticated": True,
            "managed_account_id": int(account["id"]),
            "items": items,
            "upcoming": [
                item
                for item in items
                if item["status"] in {"scheduled", "waiting", "starting"}
            ],
            "active": next(
                (item for item in items if item["status"] == "running"),
                None,
            ),
            "history": [
                item
                for item in items
                if item["status"] in TERMINAL_STATUSES
            ],
        }

    @app.post("/me/automation-schedules")
    def create_automation_schedule(
        request: Request,
        body: CreateAutomationScheduleRequest,
    ) -> dict[str, Any]:
        account = _current_account(request)
        if not bool(account.get("has_trading_api_token", False)):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Save a valid Deriv trade-scope credential before scheduling "
                    "automated trading"
                ),
            )
        try:
            scheduled_local, scheduled_utc = local_schedule_to_utc(
                body.date,
                body.time,
                body.timezone,
            )
            if scheduled_utc <= utc_now() + timedelta(seconds=5):
                raise ValueError(
                    "Scheduled time must be at least a few seconds in the future"
                )
            frozen = canonical_strategy_snapshot(
                body.strategy_snapshot,
                stake=body.stake,
                take_profit=body.take_profit,
                stop_loss=body.stop_loss,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        schedule_id = str(uuid4())
        now = utc_now()
        with base_api.DATABASE.session() as session:
            session.add(
                AutomationSchedule(
                    id=schedule_id,
                    managed_account_id=int(account["id"]),
                    strategy_name=str(body.strategy_name).strip()[:120],
                    strategy_source=str(body.strategy_source or "saved").strip()[:40],
                    strategy_snapshot=frozen,
                    timezone=str(body.timezone).strip(),
                    scheduled_local=scheduled_local,
                    scheduled_for_utc=scheduled_utc,
                    stake_amount=_finite_money(body.stake, "Stake"),
                    take_profit=_finite_money(body.take_profit, "Take profit"),
                    stop_loss=_finite_money(body.stop_loss, "Stop loss"),
                    overlap_policy=str(body.overlap_policy).lower(),
                    status="scheduled",
                    status_reason="Persistent server schedule created",
                    created_at=now,
                    updated_at=now,
                )
            )

        base_api.REPOSITORY.audit(
            "AUTOMATION_SCHEDULE_CREATED",
            str(account.get("account_id_masked") or "personal_dashboard"),
            request.client.host if request.client else "unknown",
            {
                "schedule_id": schedule_id,
                "managed_account_id": int(account["id"]),
                "scheduled_for_utc": scheduled_utc.isoformat(),
                "timezone": body.timezone,
                "overlap_policy": body.overlap_policy,
            },
        )
        _queue_private_schedule_alert(schedule_id, "created")
        with base_api.DATABASE.session() as session:
            created = session.get(AutomationSchedule, schedule_id)
            if created is None:
                raise HTTPException(
                    status_code=500,
                    detail="Scheduled session was not persisted",
                )
            return {
                "success": True,
                "schedule": _schedule_dict(created),
            }

    @app.post("/me/automation-schedules/{schedule_id}/cancel")
    def cancel_automation_schedule(
        request: Request,
        schedule_id: str,
    ) -> dict[str, Any]:
        account = _current_account(request)
        now = utc_now()
        with base_api.DATABASE.session() as session:
            row = session.get(
                AutomationSchedule,
                str(schedule_id),
                with_for_update=True,
            )
            if row is None or int(row.managed_account_id) != int(account["id"]):
                raise HTTPException(
                    status_code=404,
                    detail="Scheduled session not found",
                )
            if row.status == "running":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This scheduled session is already running. Use Stop Trading "
                        "to end the active session."
                    ),
                )
            if row.status in TERMINAL_STATUSES:
                return {"success": True, "schedule": _schedule_dict(row)}
            row.status = "cancelled"
            row.status_reason = "Cancelled by trader before execution"
            row.cancelled_at = now
            row.completed_at = now
            row.claimed_by = ""
            row.claim_expires_at = None
            row.updated_at = now
            payload = _schedule_dict(row)

        base_api.REPOSITORY.audit(
            "AUTOMATION_SCHEDULE_CANCELLED",
            str(account.get("account_id_masked") or "personal_dashboard"),
            request.client.host if request.client else "unknown",
            {
                "schedule_id": schedule_id,
                "managed_account_id": int(account["id"]),
            },
        )
        _queue_private_schedule_alert(schedule_id, "cancelled")
        return {"success": True, "schedule": payload}

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def automation_scheduler_lifespan(lifespan_app: Any):
        async with previous_lifespan(lifespan_app) as state:
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                _scheduler_loop(stop_event),
                name="action5-automation-scheduler",
            )
            app.state.automation_scheduler_stop_event = stop_event
            app.state.automation_scheduler_task = task
            try:
                yield state
            finally:
                stop_event.set()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    task.cancel()
                except asyncio.CancelledError:
                    pass

    app.router.lifespan_context = automation_scheduler_lifespan
    app.state.automation_scheduler_action5_installed = True
    app.state.automation_scheduler_action5_version = (
        "persistent-server-scheduler-v1"
    )
    _INSTALLED = True
