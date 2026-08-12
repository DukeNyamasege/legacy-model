from __future__ import annotations

import asyncio
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app import netlify_worker_bridge as bridge
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False


def _repair_tasks(bot: RFDir5TradingBot) -> dict[int, asyncio.Task[Any]]:
    tasks = getattr(bot, "_seamless_execution_repair_tasks", None)
    if not isinstance(tasks, dict):
        tasks = {}
        bot._seamless_execution_repair_tasks = tasks
    return tasks


def _schedule_runtime_repair(bot: RFDir5TradingBot, managed_id: int) -> None:
    tasks = _repair_tasks(bot)
    current = tasks.get(int(managed_id))
    if current is not None and not current.done():
        return

    async def repair() -> None:
        try:
            await asyncio.sleep(0.30)
            account = bot.repository.managed_account(int(managed_id)) or {}
            if not bool(account.get("enabled")):
                return
            await bot.validate_accounts()
            bot._sync_clients_with_runtime_accounts()
            await bot._ensure_sessions_for_valid_clients()
            direct_runtime._refresh_direct_accounts(
                bot,
                require_connected=False,
                fail_invalid=False,
            )
            bot.logger.info(
                "CUSTOM_RUNTIME_AUTO_REPAIR managed_id=%s enabled_preserved=true "
                "next_action=await_private_ready_or_condition",
                int(managed_id),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            bot.logger.warning(
                "CUSTOM_RUNTIME_AUTO_REPAIR_DEFERRED managed_id=%s error_type=%s "
                "enabled_preserved=true retry=next_refresh",
                int(managed_id),
                type(exc).__name__,
            )
        finally:
            tasks.pop(int(managed_id), None)

    try:
        task = asyncio.create_task(
            repair(),
            name=f"custom_runtime_repair_{int(managed_id)}",
        )
    except RuntimeError:
        return
    tasks[int(managed_id)] = task


def install_seamless_execution_recovery() -> None:
    """Never convert a runtime/session fault into an account-level Stop.

    Manual Stop and TP/SL remain authoritative database lifecycle stops. Any
    execution preparation, ownership synchronization, provider-session or other
    runtime fault keeps the account enabled, drops only stale hot runtime state,
    reconnects, and lets the next refresh rebuild the exact account session.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    previous_fail_handler = direct_runtime._fail_closed

    def reconnect_without_stopping(
        bot: RFDir5TradingBot,
        managed_id: int,
        reason: str,
        *,
        log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
    ) -> None:
        account = bot.repository.managed_account(int(managed_id)) or {}
        if not bool(account.get("enabled")):
            # A manual Stop or TP/SL has already disabled execution. Never revive it.
            bot.logger.info(
                "CUSTOM_RUNTIME_REPAIR_SKIPPED managed_id=%s account_enabled=false",
                int(managed_id),
            )
            return

        bridge._schedule_private_reconnect(bot, int(managed_id))
        bridge._drop_hot_runtime_only(bot, int(managed_id))
        bot._set_account_execution_status(
            int(managed_id),
            "reconnecting",
            "Execution session is reconnecting automatically; Auto Trading remains active.",
        )
        bot.logger.warning(
            "%s managed_id=%s enabled_preserved=true lifecycle_stop=false "
            "automatic_reconnect=true reason=%s",
            log_event,
            int(managed_id),
            str(reason or "runtime synchronization fault")[:140],
        )
        _schedule_runtime_repair(bot, int(managed_id))
        try:
            bridge._schedule_dashboard_wakeup(bot)
        except Exception:
            pass

    # Keep a reference only for diagnostics/tests. Runtime faults no longer invoke
    # it because the old handler could disable the managed account.
    direct_runtime._previous_stop_on_runtime_failure = previous_fail_handler
    direct_runtime._fail_closed = reconnect_without_stopping
    RFDir5TradingBot._seamless_execution_recovery_installed = True
    _INSTALLED = True
