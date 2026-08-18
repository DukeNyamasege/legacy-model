from __future__ import annotations

"""Final authority for second-precision scheduled trading.

Action 5 already owns persistent scheduling and exactly-once claiming.  This layer
keeps that architecture but closes the hybrid-runtime gaps introduced later:

* a due schedule is an explicit future Start and therefore clears a previous user
  hard-stop sentinel before the existing worker is armed;
* the scheduler loop is sub-second so an HH:MM:SS wall-clock is admitted in the
  requested second rather than several seconds later;
* completed scheduled sessions are annotated with their settled session P/L and
  run counts so schedule cards can show the actual outcome;
* schedule payloads expose those result fields without adding another database
  table or making the browser authoritative.
"""

import asyncio
import logging
import os
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select

import app.api as base_api
import app.automation_scheduler_action5 as action5
from app.automation_schedule_models import AutomationSchedule
from app.direct_execution_hard_stop_state import clear_direct_hard_stop
from app.models import Trade, utc_now

LOGGER = logging.getLogger("legacy_model.automation_scheduler_v2")
_INSTALLED = False
_RESULT_MARKER = "Session result:"
_RESULT_RE = re.compile(
    r"Session result:\s*([+-]?\d+(?:\.\d+)?)\s+USD\s*·\s*(\d+)\s+runs\s*·\s*(\d+)W/(\d+)L\s*·\s*([^|]+)$",
    re.IGNORECASE,
)


def _result_label(reason: str, profit: float) -> str:
    text = str(reason or "").strip().lower()
    if "take profit" in text or "take_profit" in text or "tp hit" in text:
        return "Take profit hit"
    if "stop loss" in text or "stop_loss" in text or "sl hit" in text:
        return "Stop loss hit"
    if "user stop" in text or "stopped by user" in text or "manual stop" in text:
        return "Stopped by trader"
    if profit > 0:
        return "Finished in profit"
    if profit < 0:
        return "Finished in loss"
    return "Session finished"


def _attach_finished_session_results() -> None:
    """Persist one immutable summary after all purchased contracts have settled."""

    with base_api.DATABASE.session() as session:
        schedules = list(
            session.scalars(
                select(AutomationSchedule)
                .where(
                    AutomationSchedule.status.in_(["completed", "failed"]),
                    AutomationSchedule.started_at.is_not(None),
                )
                .order_by(AutomationSchedule.completed_at.desc())
                .limit(100)
            ).all()
        )
        for schedule in schedules:
            if _RESULT_MARKER.lower() in str(schedule.status_reason or "").lower():
                continue
            started = schedule.started_at
            completed = schedule.completed_at or utc_now()
            if started is None:
                continue
            # Purchase time, not settlement time, defines membership in a scheduled
            # session. A contract bought before Stop remains part of that session.
            rows = list(
                session.scalars(
                    select(Trade).where(
                        Trade.managed_account_id == int(schedule.managed_account_id),
                        Trade.purchase_time >= started,
                        Trade.purchase_time <= completed + timedelta(seconds=2),
                    )
                ).all()
            )
            if any(str(row.outcome or "OPEN").upper() == "OPEN" for row in rows):
                # Do not freeze P/L until the last already-purchased contract settles.
                continue
            profit = round(sum(float(row.profit or 0.0) for row in rows), 2)
            wins = sum(1 for row in rows if str(row.outcome or "").upper() == "WIN")
            losses = sum(1 for row in rows if str(row.outcome or "").upper() == "LOSS")
            base_reason = str(schedule.status_reason or "Trading stopped").strip()
            label = _result_label(base_reason, profit)
            result = (
                f"{_RESULT_MARKER} {profit:+.2f} USD · {len(rows)} runs · "
                f"{wins}W/{losses}L · {label}"
            )
            schedule.status_reason = f"{base_reason} | {result}"[:2000]
            schedule.updated_at = utc_now()


def install_automation_scheduler_v2_authority() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_apply = action5._apply_schedule_strategy
    original_reconcile = action5._reconcile_running_schedules
    original_schedule_dict = action5._schedule_dict

    def apply_schedule_strategy_v2(schedule_id: str) -> tuple[bool, str]:
        # A schedule is a deliberate future Start. It must override a *previous*
        # manual Stop fence, otherwise account.enabled=True would still be blocked
        # by direct_execution_worker_fence at the final pre-BUY boundary.
        with base_api.DATABASE.session() as session:
            schedule = session.get(AutomationSchedule, str(schedule_id))
            if schedule is not None and schedule.status == "starting":
                clear_direct_hard_stop(session, int(schedule.managed_account_id))
        return original_apply(schedule_id)

    def reconcile_running_schedules_v2() -> None:
        original_reconcile()
        try:
            _attach_finished_session_results()
        except Exception:
            LOGGER.exception("AUTOMATION_SCHEDULE_RESULT_RECONCILE_FAILED")

    def schedule_dict_v2(row: AutomationSchedule) -> dict[str, Any]:
        payload = dict(original_schedule_dict(row))
        reason = str(payload.get("status_reason") or "")
        match = _RESULT_RE.search(reason.split("|", 1)[-1].strip())
        if match:
            payload["result_profit"] = float(match.group(1))
            payload["result_runs"] = int(match.group(2))
            payload["result_wins"] = int(match.group(3))
            payload["result_losses"] = int(match.group(4))
            payload["result_label"] = match.group(5).strip()
        else:
            payload["result_profit"] = None
            payload["result_runs"] = None
            payload["result_wins"] = None
            payload["result_losses"] = None
            payload["result_label"] = ""
        return payload

    async def scheduler_loop_v2(stop_event: asyncio.Event) -> None:
        interval = max(
            0.10,
            float(os.getenv("AUTOMATION_SCHEDULER_INTERVAL_SECONDS", "0.25")),
        )
        LOGGER.warning(
            "AUTOMATION_SCHEDULER_V2_ACTIVE interval_seconds=%.2f precision=seconds "
            "future_only=true hard_stop_clear_on_due=true result_ledger=true",
            interval,
        )
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(action5.run_scheduler_cycle)
            except Exception:
                LOGGER.exception("AUTOMATION_SCHEDULER_V2_CYCLE_FAILED")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    action5._apply_schedule_strategy = apply_schedule_strategy_v2
    action5._reconcile_running_schedules = reconcile_running_schedules_v2
    action5._schedule_dict = schedule_dict_v2
    action5._scheduler_loop = scheduler_loop_v2
    action5.AUTOMATION_SCHEDULER_V2 = True
    _INSTALLED = True
