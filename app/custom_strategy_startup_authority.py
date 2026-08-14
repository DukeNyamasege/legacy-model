from __future__ import annotations

import asyncio
import time
from typing import Any

from app.account_mode_execution_lock import STARTING_LIKE_STATUSES
from app.models import ManagedAccount, utc_now
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_ORIGINAL_VALIDATE: Any = None
_ORIGINAL_REFRESH: Any = None
_ORIGINAL_WAIT_FOR_ACTIVE: Any = None
_STARTUP_FAILURE_LIMIT = 3
_STARTUP_FAILURE_WINDOW_SECONDS = 90.0
_FAST_START_POLL_SECONDS = 0.5
_IDLE_NO_START_POLL_SECONDS = 1.0


def _represented_managed_ids(bot: RFDir5TradingBot) -> set[int]:
    """Return accounts that have an actual private-session runtime object.

    `valid_clients` alone is intentionally not enough. Validation can add an
    account to that list before `_ensure_sessions_for_valid_clients()` has created
    its ClientSession. Treating that intermediate list membership as ready would
    allow the global revision fast-path to skip the very session creation Start
    is waiting for.
    """

    represented: set[int] = set()
    for session in list(getattr(bot, "sessions", {}).values()):
        try:
            managed_id = getattr(session, "managed_account_id", None)
            if managed_id is not None:
                represented.add(int(managed_id))
        except Exception:
            continue
    return represented


def _unrepresented_startup_ids(bot: RFDir5TradingBot) -> list[int]:
    represented = _represented_managed_ids(bot)
    pending: list[int] = []
    try:
        rows = bot.repository.list_managed_accounts()
    except Exception:
        return pending
    for row in rows:
        status = str(getattr(row, "execution_status", "inactive") or "inactive").strip().lower()
        if not bool(getattr(row, "enabled", False)):
            continue
        if status not in STARTING_LIKE_STATUSES:
            continue
        managed_id = int(getattr(row, "id"))
        if managed_id not in represented:
            pending.append(managed_id)
    return pending


def _failure_state(bot: RFDir5TradingBot) -> dict[int, dict[str, float | int]]:
    state = getattr(bot, "_custom_startup_failures", None)
    if not isinstance(state, dict):
        state = {}
        bot._custom_startup_failures = state
    return state


def _record_startup_failure(bot: RFDir5TradingBot, managed_id: int) -> int:
    state = _failure_state(bot)
    now = time.monotonic()
    previous = state.get(int(managed_id)) or {}
    previous_at = float(previous.get("at") or 0.0)
    previous_count = int(previous.get("count") or 0)
    if now - previous_at > _STARTUP_FAILURE_WINDOW_SECONDS:
        previous_count = 0
    count = previous_count + 1
    state[int(managed_id)] = {"count": count, "at": now}
    return count


def _clear_startup_failure(bot: RFDir5TradingBot, managed_id: int) -> None:
    _failure_state(bot).pop(int(managed_id), None)


def _surface_startup_error(
    bot: RFDir5TradingBot,
    managed_id: int,
    exc: BaseException,
) -> None:
    reason = (
        "Trading stopped: authenticated account execution session could not initialize "
        f"after {_STARTUP_FAILURE_LIMIT} attempts ({type(exc).__name__}). Reconnect the "
        "Deriv account if this persists."
    )[:160]
    try:
        with bot.repository.database.session() as session:
            row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
            if row is None:
                return
            row.enabled = False
            row.execution_status = "error"
            row.execution_status_reason = reason
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()
    except Exception:
        bot.logger.exception(
            "CUSTOM_STARTUP_VISIBLE_ERROR_WRITE_FAILED managed_id=%s",
            int(managed_id),
        )
        return
    bot.logger.error(
        "CUSTOM_RUNTIME_STARTUP_FAILED managed_id=%s attempts=%s auto_retry=false error_type=%s",
        int(managed_id),
        _STARTUP_FAILURE_LIMIT,
        type(exc).__name__,
    )


def install_custom_strategy_startup_authority() -> None:
    """Guarantee that an explicit Start is consumed quickly and visibly.

    The base worker uses a global MAX(ManagedAccount.updated_at) revision and, when
    no accounts are active, an additional 5-10 second idle sleep. Those are useful
    housekeeping optimisations but are too slow for a manual Start button. An
    enabled startup-state account without a concrete private ClientSession is an
    independent refresh signal and bypasses both delays.

    Startup validation failures are bounded. The UI may show a retry state for
    transient failures, but the original API `starting` message can never survive
    indefinitely without either runtime progress or a visible ERROR.
    """

    global _INSTALLED, _ORIGINAL_VALIDATE, _ORIGINAL_REFRESH, _ORIGINAL_WAIT_FOR_ACTIVE
    if _INSTALLED:
        return

    _ORIGINAL_VALIDATE = RFDir5TradingBot.validate_accounts
    _ORIGINAL_REFRESH = RFDir5TradingBot._refresh_runtime_accounts_if_needed
    _ORIGINAL_WAIT_FOR_ACTIVE = RFDir5TradingBot._wait_for_active_execution_account

    async def validate_with_startup_visibility(self: RFDir5TradingBot) -> None:
        startup_ids = _unrepresented_startup_ids(self)
        for managed_id in startup_ids:
            self._set_account_execution_status(
                int(managed_id),
                "validating",
                "Validating authenticated Deriv account execution access",
            )
        try:
            await _ORIGINAL_VALIDATE(self)
        except Exception as exc:
            for managed_id in startup_ids:
                account = self.repository.managed_account(int(managed_id)) or {}
                if not bool(account.get("enabled")):
                    _clear_startup_failure(self, int(managed_id))
                    continue
                attempts = _record_startup_failure(self, int(managed_id))
                if attempts >= _STARTUP_FAILURE_LIMIT:
                    _surface_startup_error(self, int(managed_id), exc)
                else:
                    self._set_account_execution_status(
                        int(managed_id),
                        "reconnecting",
                        "Account startup validation failed temporarily; retrying automatically",
                    )
                    self.logger.warning(
                        "CUSTOM_RUNTIME_STARTUP_RETRY managed_id=%s attempt=%s/%s error_type=%s",
                        int(managed_id),
                        attempts,
                        _STARTUP_FAILURE_LIMIT,
                        type(exc).__name__,
                    )
            raise
        else:
            for managed_id in startup_ids:
                _clear_startup_failure(self, int(managed_id))

    async def refresh_with_explicit_start_pickup(self: RFDir5TradingBot) -> None:
        startup_ids = _unrepresented_startup_ids(self)
        if not startup_ids:
            await _ORIGINAL_REFRESH(self)
            return

        self.logger.info(
            "CUSTOM_RUNTIME_EXPLICIT_START_PICKUP managed_ids=%s revision_bypass=true",
            ",".join(str(item) for item in startup_ids),
        )
        await self.validate_accounts()
        self._sync_clients_with_runtime_accounts()
        await self._ensure_sessions_for_valid_clients()
        self._managed_accounts_revision = self.repository.managed_accounts_revision()
        self._runtime_mode_cache = self.repository.runtime_mode()

    async def wait_for_active_execution_account_fast(self: RFDir5TradingBot) -> bool:
        """Wake an idle worker almost immediately when the API commits Start."""

        self.repository.set_status("IDLE", "NO_ACTIVE_TRADING_ACCOUNTS")
        self.logger.info(
            "ACCOUNT_EXECUTION_IDLE reason=no_active_auto_trading_accounts "
            "action=waiting_for_start fast_poll_seconds=%.2f",
            _FAST_START_POLL_SECONDS,
        )
        while self.is_running and not self.valid_clients:
            startup_ids = _unrepresented_startup_ids(self)
            if not startup_ids:
                await asyncio.sleep(_IDLE_NO_START_POLL_SECONDS)
                continue
            self.logger.info(
                "CUSTOM_RUNTIME_FAST_START_WAKEUP managed_ids=%s poll_seconds=%.2f",
                ",".join(str(item) for item in startup_ids),
                _FAST_START_POLL_SECONDS,
            )
            try:
                # Use the final wrapped refresh so revision bypass, validation,
                # client-state sync and concrete ClientSession creation happen as
                # one startup operation.
                await self._refresh_runtime_accounts_if_needed()
            except Exception as exc:
                self.logger.warning(
                    "CUSTOM_RUNTIME_FAST_START_DEFERRED managed_ids=%s error_type=%s",
                    ",".join(str(item) for item in startup_ids),
                    type(exc).__name__,
                )
            if self.valid_clients:
                break
            await asyncio.sleep(_FAST_START_POLL_SECONDS)
        return bool(self.valid_clients)

    RFDir5TradingBot.validate_accounts = validate_with_startup_visibility
    RFDir5TradingBot._refresh_runtime_accounts_if_needed = refresh_with_explicit_start_pickup
    RFDir5TradingBot._wait_for_active_execution_account = wait_for_active_execution_account_fast
    RFDir5TradingBot._custom_strategy_startup_authority_installed = True
    RFDir5TradingBot._custom_strategy_fast_start_poll_seconds = _FAST_START_POLL_SECONDS
    _INSTALLED = True
