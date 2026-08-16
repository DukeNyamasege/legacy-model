from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any

from app import custom_strategy_connection_stampede_guard as connection_guard
from app import custom_strategy_direct_runtime as direct_runtime
from app import custom_strategy_instant_start as instant_start
from app import private_websocket_rate_limit as private_ws
from app.account_mode_execution_lock import account_allows_new_execution
from app.account_scoped_websocket_runtime import _promote_embedded_oauth_payload
from app.oauth_client import refresh_access_token, token_is_expiring
from app.rf_dir5_bot import RFDir5TradingBot
from app.token_store import decrypt_auth_payload, encrypt_auth_payload


_INSTALLED = False
_ORIGINAL_VALIDATE: Any = None
_ORIGINAL_RUN: Any = None

_WATCHDOG_INTERVAL_SECONDS = 2.0
_STALLED_WAKE_SECONDS = 8.0
_STALLED_RECYCLE_SECONDS = 30.0
_RECYCLE_COOLDOWN_SECONDS = 30.0
_OAUTH_REFRESH_WITHIN_SECONDS = 60
_OAUTH_REFRESH_TIMEOUT_SECONDS = 8.0
_OAUTH_RETRY_SECONDS = 20.0


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _oauth_refresh_attempts(bot: RFDir5TradingBot) -> dict[int, float]:
    state = getattr(bot, "_vps_oauth_refresh_attempts", None)
    if not isinstance(state, dict):
        state = {}
        bot._vps_oauth_refresh_attempts = state
    return state


async def _refresh_expiring_oauth_credentials(bot: RFDir5TradingBot) -> None:
    """Refresh only an execution account whose saved OAuth access is expiring.

    Instant Start intentionally skips the old provider account-list sweep. That is
    correct for responsiveness, but it also bypassed the older OAuth-refreshing
    account loader. Refreshing here restores that one required credential step
    without putting ordinary valid Starts behind provider REST I/O.
    """

    attempts = _oauth_refresh_attempts(bot)
    now = time.monotonic()
    for row in bot.repository.list_managed_accounts():
        if not account_allows_new_execution(row):
            continue
        managed_id = int(_row_value(row, "id"))
        try:
            payload = decrypt_auth_payload(
                str(_row_value(row, "token_secret", "") or ""),
                bot.encryption_key,
            )
            payload = _promote_embedded_oauth_payload(payload)
        except Exception:
            continue

        if str(payload.get("auth_type") or "").strip().lower() != "oauth":
            continue
        try:
            expiring = token_is_expiring(
                payload,
                within_seconds=_OAUTH_REFRESH_WITHIN_SECONDS,
            )
        except Exception:
            expiring = True
        if not expiring:
            attempts.pop(managed_id, None)
            continue

        refresh_token_value = str(
            payload.get("refresh_token") or payload.get("oauth_refresh_token") or ""
        ).strip()
        if not refresh_token_value:
            bot._set_account_execution_status(
                managed_id,
                "token_required",
                "Deriv OAuth session expired and cannot be refreshed. Log in again with trade permission.",
            )
            continue

        previous_attempt = float(attempts.get(managed_id, 0.0) or 0.0)
        if now - previous_attempt < _OAUTH_RETRY_SECONDS:
            continue
        attempts[managed_id] = now
        bot._set_account_execution_status(
            managed_id,
            "reconnecting",
            "Refreshing Deriv execution authorization automatically.",
        )

        try:
            refreshed = await asyncio.wait_for(
                asyncio.to_thread(
                    refresh_access_token,
                    client_id=str(
                        bot.test2_config.deriv.oauth_client_id or bot.app_id
                    ),
                    refresh_token=refresh_token_value,
                ),
                timeout=_OAUTH_REFRESH_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            bot._set_account_execution_status(
                managed_id,
                "reconnecting",
                "Deriv authorization refresh was delayed; retrying automatically.",
            )
            bot.logger.warning(
                "VPS_OAUTH_REFRESH_DEFERRED managed_id=%s error_type=%s auto_retry=true",
                managed_id,
                type(exc).__name__,
            )
            continue

        payload.update(refreshed)
        # Preserve both the promoted primary OAuth fields and the compatibility
        # aliases used by accounts created before OAuth became the primary runtime.
        payload["oauth_access_token"] = str(refreshed.get("access_token") or "")
        payload["oauth_refresh_token"] = str(
            refreshed.get("refresh_token") or refresh_token_value
        )
        payload["oauth_expires_at"] = str(refreshed.get("expires_at") or "")
        payload["oauth_scope"] = str(refreshed.get("scope") or payload.get("scope") or "")
        payload["auth_source"] = "deriv_oauth"
        bot.repository.update_managed_account(
            managed_id,
            token_secret=encrypt_auth_payload(payload, bot.encryption_key),
            enabled=True,
        )
        attempts.pop(managed_id, None)
        bot._set_account_execution_status(
            managed_id,
            "connecting",
            "Deriv authorization refreshed; execution stream is reconnecting now.",
        )
        bot.logger.info(
            "VPS_OAUTH_REFRESHED managed_id=%s fresh_private_session_required=true",
            managed_id,
        )


async def _validate_with_oauth_recovery(self: RFDir5TradingBot) -> None:
    await _refresh_expiring_oauth_credentials(self)
    original = _ORIGINAL_VALIDATE
    if original is not None:
        await original(self)


def _stall_state(bot: RFDir5TradingBot) -> dict[int, dict[str, float]]:
    state = getattr(bot, "_vps_execution_stall_state", None)
    if not isinstance(state, dict):
        state = {}
        bot._vps_execution_stall_state = state
    return state


def _provider_backoff_active(row: Any) -> bool:
    reason = str(_row_value(row, "execution_status_reason", "") or "").lower()
    return "rate-limit" in reason or "rate limited" in reason


async def _recycle_stalled_private_session(
    bot: RFDir5TradingBot,
    managed_id: int,
    session: Any,
) -> None:
    """Recycle only a non-financial, disconnected account session.

    Open provider contracts are never touched. The existing global private
    connection gate remains on the bot, so this fresh attempt still honors rate
    limits and handshake concurrency.
    """

    pending: set[Any] = set(getattr(session, "pending_contracts", set()) or set())
    if pending:
        return

    token = str(getattr(session, "token", "") or "")
    task = getattr(session, "task", None)
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    websocket = getattr(session, "ws", None)
    if websocket is not None:
        with suppress(Exception):
            await websocket.close(code=1012, reason="vps_stalled_execution_recycle")

    sessions = getattr(bot, "sessions", {})
    if token and sessions.get(token) is session:
        sessions.pop(token, None)

    # Re-read this account's durable credential. If OAuth was refreshed, this
    # also replaces its old runtime key without rebuilding healthy siblings.
    connection_guard._admit_one_runtime_account(bot, managed_id)
    fresh = await connection_guard._ensure_one_private_session(
        bot,
        managed_id,
        wake=True,
    )
    direct_runtime._refresh_direct_accounts(
        bot,
        require_connected=False,
        fail_invalid=False,
    )
    bot._set_account_execution_status(
        managed_id,
        "reconnecting",
        "Execution stream was unresponsive; a fresh private session is reconnecting automatically.",
    )
    bot.logger.warning(
        "VPS_EXECUTION_STALL_RECYCLED managed_id=%s old_session_cancelled=true "
        "new_session=%s sibling_sessions_rebuilt=false open_contracts=false auto_retry=true",
        managed_id,
        fresh is not None,
    )


async def _stalled_execution_watchdog(bot: RFDir5TradingBot) -> None:
    """Prevent an enabled account from remaining 'connecting' indefinitely."""

    while bot.is_running:
        try:
            now = time.monotonic()
            state = _stall_state(bot)
            enabled_ids: set[int] = set()
            for row in bot.repository.list_managed_accounts():
                managed_id = int(_row_value(row, "id"))
                if not bool(_row_value(row, "enabled", False)):
                    state.pop(managed_id, None)
                    continue
                enabled_ids.add(managed_id)

                session = connection_guard._private_session_for_account(bot, managed_id)
                if session is not None and bool(getattr(session, "is_connected", False)):
                    state.pop(managed_id, None)
                    if connection_guard._direct_runtime_for_account(bot, managed_id) is None:
                        connection_guard._schedule_targeted_runtime_repair(bot, managed_id)
                    continue

                entry = state.setdefault(
                    managed_id,
                    {
                        "disconnected_since": now,
                        "last_wake": 0.0,
                        "last_recycle": 0.0,
                    },
                )
                disconnected_for = now - float(entry.get("disconnected_since") or now)

                # Provider rate-limit penalties are authoritative. Repeatedly
                # waking/recycling during a 429/1015 backoff would make recovery
                # slower and could extend the provider penalty.
                if _provider_backoff_active(row):
                    continue

                task = getattr(session, "task", None) if session is not None else None
                task_alive = bool(task is not None and not task.done())
                if session is None or not task_alive:
                    if disconnected_for >= _STALLED_WAKE_SECONDS:
                        connection_guard._schedule_targeted_runtime_repair(bot, managed_id)
                    continue

                if disconnected_for >= _STALLED_WAKE_SECONDS:
                    last_wake = float(entry.get("last_wake") or 0.0)
                    if now - last_wake >= _STALLED_WAKE_SECONDS:
                        entry["last_wake"] = now
                        private_ws.wake_private_connection(session)
                        bot._set_account_execution_status(
                            managed_id,
                            "reconnecting",
                            "Execution stream is delayed; reconnect requested automatically.",
                        )
                        bot.logger.info(
                            "VPS_EXECUTION_STALL_WAKE managed_id=%s disconnected_seconds=%.1f",
                            managed_id,
                            disconnected_for,
                        )

                if disconnected_for < _STALLED_RECYCLE_SECONDS:
                    continue
                if getattr(session, "pending_contracts", set()):
                    continue
                last_recycle = float(entry.get("last_recycle") or 0.0)
                if now - last_recycle < _RECYCLE_COOLDOWN_SECONDS:
                    continue
                entry["last_recycle"] = now
                entry["disconnected_since"] = now
                await _recycle_stalled_private_session(bot, managed_id, session)

            for managed_id in list(state):
                if managed_id not in enabled_ids:
                    state.pop(managed_id, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            bot.logger.exception("VPS_EXECUTION_STALL_WATCHDOG_FAILED")
        await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)


async def _run_with_vps_execution_recovery(self: RFDir5TradingBot) -> None:
    original = _ORIGINAL_RUN
    if original is None:
        return
    watchdog = asyncio.create_task(
        _stalled_execution_watchdog(self),
        name="vps_execution_stall_watchdog",
    )
    try:
        await original(self)
    finally:
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog


def install_vps_execution_start_recovery() -> None:
    """Final full-VPS Start/reconnect recovery authority."""

    global _INSTALLED, _ORIGINAL_VALIDATE, _ORIGINAL_RUN
    if _INSTALLED:
        return

    _ORIGINAL_VALIDATE = RFDir5TradingBot.validate_accounts
    _ORIGINAL_RUN = RFDir5TradingBot.run

    # Do not let repeated local account admission overwrite a useful provider
    # reconnect/rate-limit reason with the generic 'connecting' copy.
    instant_start.STARTING_LIKE_STATUSES = {"starting", "validating", "connecting"}
    connection_guard.STARTING_LIKE_STATUSES = {"starting", "validating", "connecting"}

    RFDir5TradingBot.validate_accounts = _validate_with_oauth_recovery
    RFDir5TradingBot.run = _run_with_vps_execution_recovery
    RFDir5TradingBot._vps_execution_start_recovery_installed = True
    RFDir5TradingBot._vps_stalled_execution_recycle_seconds = _STALLED_RECYCLE_SECONDS
    _INSTALLED = True
