from __future__ import annotations

import asyncio
import time
from typing import Any

from app import custom_execution_consistency_authority as consistency
from app import custom_strategy_connection_stampede_guard as connection_guard
from app import private_websocket_rate_limit as private_ws
from app import vps_execution_start_recovery as vps_recovery
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False

_WATCHDOG_INTERVAL_SECONDS = 2.0
_DEAD_SESSION_REPAIR_GRACE_SECONDS = 8.0
_RECONNECT_LOG_INTERVAL_SECONDS = 60.0


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _provider_backoff_active(row: Any) -> bool:
    reason = str(_row_value(row, "execution_status_reason", "") or "").lower()
    return "rate-limit" in reason or "rate limited" in reason


def _stability_state(bot: RFDir5TradingBot) -> dict[int, dict[str, float]]:
    state = getattr(bot, "_custom_connection_stability_state", None)
    if not isinstance(state, dict):
        state = {}
        bot._custom_connection_stability_state = state
    return state


async def _stable_execution_watchdog(bot: RFDir5TradingBot) -> None:
    """Repair missing/dead sessions without disturbing a live reconnect loop.

    A live ClientSession owns its queued connection slot, OTP request, provider
    backoff and WebSocket handshake. The watchdog must never wake, cancel or recycle
    that task merely because it has not connected yet.
    """

    while bot.is_running:
        try:
            now = time.monotonic()
            state = _stability_state(bot)
            enabled_ids: set[int] = set()

            for row in bot.repository.list_managed_accounts():
                managed_id = int(_row_value(row, "id"))
                if not bool(_row_value(row, "enabled", False)):
                    state.pop(managed_id, None)
                    continue
                enabled_ids.add(managed_id)

                session = connection_guard._private_session_for_account(bot, managed_id)
                connected = bool(
                    session is not None and getattr(session, "is_connected", False)
                )
                if connected:
                    state.pop(managed_id, None)
                    if connection_guard._direct_runtime_for_account(bot, managed_id) is None:
                        connection_guard._schedule_targeted_runtime_repair(bot, managed_id)
                    continue

                entry = state.setdefault(
                    managed_id,
                    {
                        "disconnected_since": now,
                        "last_repair": 0.0,
                        "last_log": 0.0,
                    },
                )
                disconnected_for = now - float(entry.get("disconnected_since") or now)

                # Provider penalties are authoritative. Do not shorten a 429/1015
                # backoff by manufacturing another connection attempt.
                if _provider_backoff_active(row):
                    continue

                task_alive = connection_guard._session_task_alive(session)

                # Critical invariant: an existing live session task already owns
                # OTP scheduling, handshake timeout, backoff and reconnection.
                # Leaving it alone prevents watchdog/recycle storms from keeping
                # dozens of accounts forever in STARTING.
                if session is not None and task_alive:
                    last_log = float(entry.get("last_log") or 0.0)
                    if (
                        disconnected_for >= _DEAD_SESSION_REPAIR_GRACE_SECONDS
                        and now - last_log >= _RECONNECT_LOG_INTERVAL_SECONDS
                    ):
                        entry["last_log"] = now
                        bot.logger.info(
                            "VPS_EXECUTION_RECONNECT_OWNED managed_id=%s "
                            "disconnected_seconds=%.1f session_task_alive=true "
                            "watchdog_wake=false session_recycle=false",
                            managed_id,
                            disconnected_for,
                        )
                    continue

                if disconnected_for < _DEAD_SESSION_REPAIR_GRACE_SECONDS:
                    continue

                last_repair = float(entry.get("last_repair") or 0.0)
                if now - last_repair < _DEAD_SESSION_REPAIR_GRACE_SECONDS:
                    continue
                entry["last_repair"] = now
                connection_guard._schedule_targeted_runtime_repair(bot, managed_id)
                bot.logger.warning(
                    "VPS_EXECUTION_DEAD_SESSION_REPAIR managed_id=%s "
                    "session_object=%s session_task_alive=false "
                    "sibling_sessions_rebuilt=false",
                    managed_id,
                    session is not None,
                )

            for managed_id in list(state):
                if managed_id not in enabled_ids:
                    state.pop(managed_id, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            bot.logger.exception("VPS_EXECUTION_STABILITY_WATCHDOG_FAILED")
        await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)


def _soft_private_reconnect(
    bot: RFDir5TradingBot,
    managed_id: int,
    reason: str,
) -> None:
    """Recover one private execution stream without force-closing a live session."""

    account = bot.repository.managed_account(int(managed_id)) or {}
    if not bool(account.get("enabled")):
        return

    bot._set_account_execution_status(
        int(managed_id),
        "reconnecting",
        "Private trading connection is recovering automatically; Auto Trading remains active.",
    )

    session = connection_guard._private_session_for_account(bot, int(managed_id))
    connected = bool(session is not None and getattr(session, "is_connected", False))
    task_alive = connection_guard._session_task_alive(session)

    # A qualified execution may wake an existing disconnected session. This is a
    # soft event handled by ClientSession; it never cancels the task or closes its
    # socket. Missing/dead sessions use the single-flight targeted repair path.
    if session is not None and task_alive and not connected:
        try:
            private_ws.wake_private_connection(session)
        except Exception:
            pass

    if session is None or not task_alive:
        connection_guard._schedule_targeted_runtime_repair(bot, int(managed_id))
    elif connection_guard._direct_runtime_for_account(bot, int(managed_id)) is None:
        connection_guard._schedule_targeted_runtime_repair(bot, int(managed_id))

    bot.logger.warning(
        "CUSTOM_EXECUTION_SOFT_RECONNECT managed_id=%s lifecycle_stop=false "
        "forced_disconnect=false public_reconnect=false session_task_alive=%s "
        "session_connected=%s reason=%s",
        int(managed_id),
        task_alive,
        connected,
        str(reason or "execution transport fault")[:140],
    )
    try:
        consistency._dashboard_wakeup(bot)
    except Exception:
        pass


def _skip_execution_driven_public_reconnect(
    bot: RFDir5TradingBot,
    reason: str,
) -> None:
    """Keep account-private faults from restarting the shared market stream.

    Public WebSocket resilience owns genuine market-stream failures independently.
    Direct account proposal/BUY preparation errors are private-session concerns.
    """

    bot.logger.info(
        "CUSTOM_PUBLIC_RECONNECT_SKIPPED source=account_private_execution "
        "public_stream_owner=public_websocket_resilience reason=%s",
        str(reason or "account private execution fault")[:140],
    )


def install_custom_strategy_connection_stability_fix() -> None:
    """Final connection invariant for the full-VPS Custom Strategy worker."""

    global _INSTALLED
    if _INSTALLED:
        return

    # vps_execution_start_recovery wraps RFDir5TradingBot.run, but resolves this
    # module-global watchdog when run() actually starts. Replace only the watchdog;
    # keep its OAuth refresh wrapper intact.
    vps_recovery._stalled_execution_watchdog = _stable_execution_watchdog

    # custom_execution_consistency_authority installs after the earlier continuity
    # layer and had reintroduced force-close + public reconnect behavior for
    # private account faults. Its nested handlers resolve these globals at runtime.
    consistency._request_private_reconnect = _soft_private_reconnect
    consistency._request_public_reconnect = _skip_execution_driven_public_reconnect

    RFDir5TradingBot._custom_strategy_connection_stability_fix_installed = True
    RFDir5TradingBot._vps_stalled_execution_recycle_seconds = None
    _INSTALLED = True
