from __future__ import annotations

"""Final execution lifecycle authority: stop only for TP, SL, or explicit user Stop.

This module is installed after every older recovery/fail-closed wrapper. Once an
account is already enabled, automatic provider/runtime failures are converted into
retry states instead of disabling execution. The independent direct hard-stop
sentinel remains the only manual-stop authority.
"""

import asyncio
import logging
from contextlib import suppress
from typing import Any

from sqlalchemy import select

from app import custom_strategy_direct_runtime as direct_runtime
from app import seamless_execution_recovery as seamless
from app.direct_execution_hard_stop_state import direct_hard_stop_active
from app.models import ManagedAccount, utc_now
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False

_ORIGINAL_SET_STATUS: Any = None
_ORIGINAL_FAIL_CLOSED: Any = None
_ORIGINAL_QUARANTINE: Any = None
_ORIGINAL_DISCARD_TOKEN: Any = None
_ORIGINAL_RUN: Any = None

_REPAIR_SCAN_SECONDS = 3.0
_TARGET_STOPS = {"take_profit", "stop_loss"}
_AUTOMATIC_STOP_STATUSES = {
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
}
_TERMINAL_REQUESTS = _TARGET_STOPS | _AUTOMATIC_STOP_STATUSES | {
    "stopped",
    "disabled",
    "inactive",
    "manual_pause",
    "real_disabled",
}


def _retry_status(status: str, reason: str) -> str:
    text = f"{status} {reason}".lower()
    if any(
        marker in text
        for marker in (
            "token",
            "credential",
            "connect",
            "socket",
            "session",
            "websocket",
            "network",
            "transport",
        )
    ):
        return "reconnecting"
    return "waiting_for_condition"


def _manual_hard_stop(repository: Test2Repository, managed_id: int) -> bool:
    try:
        with repository.database.session() as session:
            return direct_hard_stop_active(session, int(managed_id))
    except Exception:
        # Never manufacture a manual stop from a lookup failure.
        return False


def _terminal_allowed(repository: Test2Repository, managed_id: int, status: str) -> bool:
    normalized = str(status or "").strip().lower()
    if normalized in _TARGET_STOPS:
        return True
    try:
        with repository.database.session() as session:
            if direct_hard_stop_active(session, int(managed_id)):
                return True
            row = session.get(ManagedAccount, int(managed_id))
            current = str(row.execution_status or "").strip().lower() if row is not None else ""
            return current in _TARGET_STOPS
    except Exception:
        # A lookup failure is not evidence of a manual stop.
        return False


def _force_retry_state(
    repository: Test2Repository,
    managed_id: int,
    status: str,
    reason: str,
    *,
    require_enabled: bool,
) -> bool:
    """Keep a previously-started account alive without auto-starting idle accounts."""

    with repository.database.session() as session:
        row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
        if row is None:
            return False
        if direct_hard_stop_active(session, int(managed_id)):
            return False
        current = str(row.execution_status or "").strip().lower()
        if current in _TARGET_STOPS:
            return False
        if require_enabled and not bool(row.enabled):
            return False

        row.enabled = True
        row.execution_status = _retry_status(status, reason)
        row.execution_status_reason = (
            "Auto Trading remains active; automatic recovery will retry. "
            f"{reason or status or 'Temporary execution fault'}"
        )[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()
        return True


def _final_set_status(
    self: Test2Repository,
    account_id: int,
    execution_status: str,
    reason: str = "",
) -> None:
    original = _ORIGINAL_SET_STATUS
    if original is None:
        return

    requested = str(execution_status or "inactive").strip().lower()
    if requested not in _TERMINAL_REQUESTS:
        original(self, int(account_id), requested, reason)
        return

    if _terminal_allowed(self, int(account_id), requested):
        original(self, int(account_id), requested, reason)
        return

    # The rule applies only after Start. Never auto-start a row that was already
    # disabled/idle before this automatic status transition.
    if _force_retry_state(
        self,
        int(account_id),
        requested,
        reason,
        require_enabled=True,
    ):
        LOGGER.warning(
            "TP_SL_MANUAL_ONLY_STOP_BLOCKED managed_id=%s attempted_status=%s "
            "manual_hard_stop=false auto_retry=true",
            int(account_id),
            requested,
        )
        return

    original(self, int(account_id), requested, reason)


def _final_fail_closed(
    bot: RFDir5TradingBot,
    managed_id: int,
    reason: str,
    *,
    log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
) -> None:
    if _terminal_allowed(bot.repository, int(managed_id), ""):
        original = _ORIGINAL_FAIL_CLOSED
        if original is not None:
            original(bot, int(managed_id), reason, log_event=log_event)
        return

    if _force_retry_state(
        bot.repository,
        int(managed_id),
        "error",
        reason,
        require_enabled=True,
    ):
        seamless._schedule_runtime_repair(bot, int(managed_id))
        bot.logger.warning(
            "TP_SL_MANUAL_ONLY_FAIL_CLOSED_RECOVERED managed_id=%s source=%s "
            "lifecycle_stop=false auto_retry=true reason=%s",
            int(managed_id),
            log_event,
            str(reason or "")[:160],
        )
        return

    original = _ORIGINAL_FAIL_CLOSED
    if original is not None:
        original(bot, int(managed_id), reason, log_event=log_event)


def _final_quarantine(
    self: Test2Repository,
    account_id: int,
    execution_status: str,
    reason: str,
) -> None:
    original = _ORIGINAL_QUARANTINE
    if original is None:
        return

    with self.database.session() as session:
        row = session.get(ManagedAccount, int(account_id))
        was_enabled = bool(row.enabled) if row is not None else False

    original(self, int(account_id), execution_status, reason)

    if was_enabled and not _terminal_allowed(self, int(account_id), execution_status):
        _force_retry_state(
            self,
            int(account_id),
            execution_status,
            reason,
            require_enabled=False,
        )


def _final_discard_token(
    self: Test2Repository,
    account_id: int,
    *,
    reason: str,
) -> list[int]:
    original = _ORIGINAL_DISCARD_TOKEN
    if original is None:
        return []

    with self.database.session() as session:
        enabled_before = {
            int(row.id): bool(row.enabled)
            for row in session.scalars(select(ManagedAccount)).all()
        }

    affected = list(original(self, int(account_id), reason=reason) or [])
    for managed_id in affected:
        if not enabled_before.get(int(managed_id), False):
            continue
        if _terminal_allowed(self, int(managed_id), "token_required"):
            continue
        _force_retry_state(
            self,
            int(managed_id),
            "token_required",
            reason,
            require_enabled=False,
        )
    return affected


async def _repair_direct_automatic_stops(bot: RFDir5TradingBot) -> None:
    """Repair automatic direct DB disables that bypass repository status wrappers."""

    while bot.is_running:
        repaired: list[int] = []
        try:
            with bot.repository.database.session() as session:
                rows = session.scalars(
                    select(ManagedAccount).where(
                        ManagedAccount.enabled.is_(False),
                        ManagedAccount.execution_status.in_(
                            sorted(_AUTOMATIC_STOP_STATUSES)
                        ),
                    )
                ).all()
                for row in rows:
                    managed_id = int(row.id)
                    if direct_hard_stop_active(session, managed_id):
                        continue
                    status = str(row.execution_status or "error").strip().lower()
                    if status in _TARGET_STOPS:
                        continue
                    reason = str(row.execution_status_reason or status)
                    row.enabled = True
                    row.execution_status = _retry_status(status, reason)
                    row.execution_status_reason = (
                        "Auto Trading restored after an automatic lifecycle stop; "
                        f"recovery will retry. {reason}"
                    )[:160]
                    row.execution_status_updated_at = utc_now()
                    row.updated_at = utc_now()
                    repaired.append(managed_id)

            for managed_id in repaired:
                seamless._schedule_runtime_repair(bot, managed_id)
                bot.logger.warning(
                    "TP_SL_MANUAL_ONLY_STALE_AUTO_STOP_REPAIRED managed_id=%s "
                    "auto_retry=true",
                    managed_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            bot.logger.exception("TP_SL_MANUAL_ONLY_REPAIR_SCAN_FAILED")

        await asyncio.sleep(_REPAIR_SCAN_SECONDS)


async def _run_with_final_stop_authority(self: RFDir5TradingBot) -> None:
    original = _ORIGINAL_RUN
    if original is None:
        return

    repair_task = asyncio.create_task(
        _repair_direct_automatic_stops(self),
        name="tp_sl_manual_only_repair",
    )
    try:
        await original(self)
    finally:
        repair_task.cancel()
        with suppress(asyncio.CancelledError):
            await repair_task


def install_tp_sl_manual_only_authority() -> None:
    """Install the last lifecycle wrapper for the persistent worker."""

    global _INSTALLED
    global _ORIGINAL_SET_STATUS, _ORIGINAL_FAIL_CLOSED
    global _ORIGINAL_QUARANTINE, _ORIGINAL_DISCARD_TOKEN, _ORIGINAL_RUN

    if _INSTALLED:
        return

    _ORIGINAL_SET_STATUS = Test2Repository.set_managed_account_execution_status
    _ORIGINAL_FAIL_CLOSED = direct_runtime._fail_closed
    _ORIGINAL_QUARANTINE = Test2Repository.quarantine_managed_account
    _ORIGINAL_DISCARD_TOKEN = Test2Repository.discard_rejected_trading_token
    _ORIGINAL_RUN = RFDir5TradingBot.run

    Test2Repository.set_managed_account_execution_status = _final_set_status
    Test2Repository.quarantine_managed_account = _final_quarantine
    Test2Repository.discard_rejected_trading_token = _final_discard_token
    direct_runtime._fail_closed = _final_fail_closed
    RFDir5TradingBot.run = _run_with_final_stop_authority

    Test2Repository._tp_sl_manual_only_authority_installed = True
    RFDir5TradingBot._tp_sl_manual_only_authority_installed = True
    RFDir5TradingBot._tp_sl_manual_only_repair_seconds = _REPAIR_SCAN_SECONDS
    _INSTALLED = True
