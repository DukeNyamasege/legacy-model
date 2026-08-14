from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app import private_websocket_rate_limit as private_ws
from app import seamless_execution_recovery as seamless
from app.models import ManagedAccount, utc_now
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_ORIGINAL_SET_STATUS: Any = None
_ORIGINAL_FAIL_CLOSED: Any = None
_ORIGINAL_RUN: Any = None

_WATCHDOG_INTERVAL_SECONDS = 2.0
_MISSING_SESSION_GRACE_SECONDS = 4.0
_REPAIR_INTERVAL_SECONDS = 12.0

_GENERIC_STOP_STATUSES = {"stopped", "disabled", "inactive"}
_ACTIONABLE_TERMINAL_STATUSES = {
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
    "take_profit",
    "stop_loss",
    "manual_pause",
    "real_disabled",
}
_TERMINAL_STATUSES = _GENERIC_STOP_STATUSES | _ACTIONABLE_TERMINAL_STATUSES

_REASON_FALLBACKS = {
    "error": "Trading stopped because account execution failed. Press Start to retry after checking the account connection.",
    "credential_error": "Trading stopped because the Deriv trading credential was rejected. Reconnect or replace the credential.",
    "invalid_account": "Trading stopped because the authenticated Deriv account could not be validated.",
    "token_required": "Trading stopped because a valid Deriv trade-scope credential is required.",
    "bulk_execution_pat_required": "Trading stopped because the required trading credential is unavailable.",
    "contract_unavailable": "Trading stopped because the configured contract is currently unavailable for this account or market.",
    "purchase_registration_error": "Trading stopped because a confirmed purchase could not be registered safely. Review the account before restarting.",
    "insufficient_balance": "Trading paused because the account balance cannot safely fund the configured stake.",
    "purchase_insufficient_balance": "Trading paused because the account balance cannot safely fund the requested purchase.",
    "duplicate": "Trading stopped because this account conflicts with another active execution identity.",
    "take_profit": "Auto trading stopped because the configured take-profit target was reached.",
    "stop_loss": "Auto trading stopped because the configured stop-loss limit was reached.",
    "manual_pause": "Auto trading was paused manually. Resume is required before new purchases.",
    "real_disabled": "Real-account auto trading is disabled for this account mode.",
    "stopped": "Auto trading is stopped. Press Start Auto Trading to begin a new execution session.",
    "disabled": "Auto trading is disabled. Press Start Auto Trading after resolving the account state.",
    "inactive": "Auto trading is inactive. Press Start Auto Trading to begin execution.",
}


def _safe_reason(status: str, reason: str) -> str:
    text = str(reason or "").strip()
    if text:
        return text[:160]
    return _REASON_FALLBACKS.get(
        str(status or "").strip().lower(),
        "Trading stopped for a recorded account execution reason.",
    )[:160]


def _status_snapshot(repository: Test2Repository, managed_id: int) -> dict[str, Any]:
    try:
        return repository.managed_account(int(managed_id)) or {}
    except Exception:
        return {}


def _write_terminal_state(
    repository: Test2Repository,
    managed_id: int,
    status: str,
    reason: str,
) -> None:
    normalized = str(status or "error").strip().lower()[:30]
    safe_reason = _safe_reason(normalized, reason)
    with repository.database.session() as session:
        row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
        if row is None:
            return
        row.enabled = False
        row.execution_status = normalized
        row.execution_status_reason = safe_reason
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()


def _preserve_terminal_status(
    self: Test2Repository,
    account_id: int,
    execution_status: str,
    reason: str = "",
) -> None:
    """Make every automatic stop atomic and prevent later reason destruction."""

    original = _ORIGINAL_SET_STATUS
    if original is None:
        return

    requested = str(execution_status or "inactive").strip().lower()
    with self.database.session() as session:
        row = session.get(ManagedAccount, int(account_id), with_for_update=True)
        if row is None:
            return
        current = str(row.execution_status or "inactive").strip().lower()
        current_reason = str(row.execution_status_reason or "").strip()
        enabled = bool(row.enabled)

        # Validation used to turn actionable disabled states such as token_required,
        # contract_unavailable or take_profit into plain `stopped`. That erased the
        # only useful explanation the trader had. Once a terminal cause exists it
        # survives generic housekeeping until the user explicitly starts again.
        if (
            requested in _GENERIC_STOP_STATUSES
            and not enabled
            and current in _ACTIONABLE_TERMINAL_STATUSES
        ):
            row.execution_status_reason = _safe_reason(current, current_reason)
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            LOGGER.warning(
                "ACCOUNT_STOP_REASON_PRESERVED managed_id=%s status=%s attempted_status=%s reason=%s",
                int(account_id),
                current,
                requested,
                row.execution_status_reason,
            )
            return

        if requested in _TERMINAL_STATUSES:
            row.enabled = False
            row.execution_status = requested[:30]
            row.execution_status_reason = _safe_reason(requested, reason)
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
            LOGGER.warning(
                "ACCOUNT_EXECUTION_TERMINAL_STATE managed_id=%s status=%s reason=%s",
                int(account_id),
                requested,
                row.execution_status_reason,
            )
            return

    # Running/start/reconnect statuses keep all existing lifecycle and manual-stop
    # promotion guards. Only terminal transitions are centralized above.
    original(self, int(account_id), requested, reason)


def _repair_disabled_reason(repository: Test2Repository, managed_id: int) -> None:
    with repository.database.session() as session:
        row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
        if row is None or bool(row.enabled):
            return
        status = str(row.execution_status or "inactive").strip().lower()
        reason = str(row.execution_status_reason or "").strip()
        if reason:
            return
        row.execution_status_reason = _safe_reason(status, "")
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()
        LOGGER.error(
            "ACCOUNT_STOP_REASON_REPAIRED managed_id=%s status=%s reason=%s",
            int(managed_id),
            status,
            row.execution_status_reason,
        )


def _fail_closed_with_durable_reason(
    bot: RFDir5TradingBot,
    managed_id: int,
    reason: str,
    *,
    log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
) -> None:
    original = _ORIGINAL_FAIL_CLOSED
    if original is None:
        return

    original(bot, int(managed_id), reason, log_event=log_event)
    account = _status_snapshot(bot.repository, int(managed_id))
    if bool(account.get("enabled")):
        # Recoverable transport/runtime faults deliberately remain enabled.
        return

    status = str(account.get("execution_status") or "error").strip().lower()
    persisted_reason = str(account.get("execution_status_reason") or "").strip()
    if status in _GENERIC_STOP_STATUSES:
        # _fail_closed is never a manual Stop button. A generic stop here means
        # the actual execution failure was lost, so promote it to a visible ERROR.
        _write_terminal_state(
            bot.repository,
            int(managed_id),
            "error",
            reason or persisted_reason or "Account execution failed safely.",
        )
        status = "error"
    elif not persisted_reason:
        _write_terminal_state(
            bot.repository,
            int(managed_id),
            status if status in _TERMINAL_STATUSES else "error",
            reason,
        )

    bot.logger.error(
        "ACCOUNT_AUTOTRADE_STOP_RECORDED managed_id=%s status=%s reason=%s source=%s",
        int(managed_id),
        status,
        _safe_reason(status, reason or persisted_reason),
        log_event,
    )


def _private_session_for_account(bot: RFDir5TradingBot, managed_id: int) -> Any | None:
    for session in list(getattr(bot, "sessions", {}).values()):
        try:
            if int(getattr(session, "managed_account_id", -1)) == int(managed_id):
                return session
        except (TypeError, ValueError):
            continue
    return None


def _liveness_state(bot: RFDir5TradingBot) -> dict[int, dict[str, float]]:
    state = getattr(bot, "_execution_liveness_watchdog_state", None)
    if not isinstance(state, dict):
        state = {}
        bot._execution_liveness_watchdog_state = state
    return state


async def _execution_liveness_watchdog(bot: RFDir5TradingBot) -> None:
    """Keep a started account alive or explain exactly why it cannot execute."""

    while bot.is_running:
        try:
            rows = bot.repository.list_managed_accounts()
            now = time.monotonic()
            state = _liveness_state(bot)
            enabled_ids: set[int] = set()

            for row in rows:
                managed_id = int(getattr(row, "id"))
                enabled = bool(getattr(row, "enabled", False))
                status = str(
                    getattr(row, "execution_status", "inactive") or "inactive"
                ).strip().lower()

                if not enabled:
                    state.pop(managed_id, None)
                    _repair_disabled_reason(bot.repository, managed_id)
                    continue

                enabled_ids.add(managed_id)
                session = _private_session_for_account(bot, managed_id)
                if session is not None and bool(getattr(session, "is_connected", False)):
                    state.pop(managed_id, None)
                    continue

                entry = state.setdefault(
                    managed_id,
                    {"missing_since": now, "last_repair": 0.0},
                )
                missing_for = now - float(entry.get("missing_since") or now)
                if session is not None:
                    try:
                        private_ws.wake_private_connection(session)
                    except Exception:
                        pass

                if missing_for < _MISSING_SESSION_GRACE_SECONDS:
                    continue
                last_repair = float(entry.get("last_repair") or 0.0)
                if now - last_repair < _REPAIR_INTERVAL_SECONDS:
                    continue
                entry["last_repair"] = now

                # This status write goes through the existing account-mode lock, so
                # a manual Stop racing this watchdog can never be promoted again.
                bot._set_account_execution_status(
                    managed_id,
                    "reconnecting",
                    "Execution watchdog detected a missing private trading session; reconnecting automatically. Auto Trading remains active.",
                )
                seamless._schedule_runtime_repair(bot, managed_id)
                bot.logger.warning(
                    "ACCOUNT_EXECUTION_LIVENESS_REPAIR managed_id=%s previous_status=%s "
                    "missing_seconds=%.1f session_object=%s lifecycle_stop=false auto_retry=true",
                    managed_id,
                    status,
                    missing_for,
                    session is not None,
                )

            for managed_id in list(state):
                if managed_id not in enabled_ids:
                    state.pop(managed_id, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            bot.logger.exception("ACCOUNT_EXECUTION_LIVENESS_WATCHDOG_FAILED")
        await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)


async def _run_with_execution_liveness(self: RFDir5TradingBot) -> None:
    original = _ORIGINAL_RUN
    if original is None:
        return
    watchdog = asyncio.create_task(
        _execution_liveness_watchdog(self),
        name="account_execution_liveness_watchdog",
    )
    try:
        await original(self)
    finally:
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog


def install_execution_stop_reason_authority() -> None:
    """Final authority: no unexplained stop and no silent started-account stall."""

    global _INSTALLED, _ORIGINAL_SET_STATUS, _ORIGINAL_FAIL_CLOSED, _ORIGINAL_RUN
    if _INSTALLED:
        return

    _ORIGINAL_SET_STATUS = Test2Repository.set_managed_account_execution_status
    _ORIGINAL_FAIL_CLOSED = direct_runtime._fail_closed
    _ORIGINAL_RUN = RFDir5TradingBot.run

    Test2Repository.set_managed_account_execution_status = _preserve_terminal_status
    direct_runtime._fail_closed = _fail_closed_with_durable_reason
    RFDir5TradingBot.run = _run_with_execution_liveness
    RFDir5TradingBot._execution_stop_reason_authority_installed = True
    RFDir5TradingBot._execution_liveness_watchdog_interval = _WATCHDOG_INTERVAL_SECONDS
    _INSTALLED = True
