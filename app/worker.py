from __future__ import annotations

import asyncio
import signal

from app.account_execution_diagnostics import install_account_execution_diagnostics
from app.account_execution_feedback import install_account_execution_feedback
from app.account_lifecycle import install_worker_account_lifecycle
from app.deployment_announcement import install_dynamic_deployment_announcement
from app.hybrid_data_integrity import install_hybrid_data_integrity
from app.hybrid_digit_put import install_hybrid_digit_put_strategy
from app.hybrid_recent_digit_bias import install_recent_digit_bias_strategy
from app.hybrid_runtime_config import install_hybrid_runtime_config
from app.hybrid_safety import install_hybrid_worker_safety
from app.production_worker_integration import install_production_worker_integration
from app.rf_dir5_bot import RFDir5TradingBot
from app.stake_only_balance_policy import install_stake_only_balance_policy
from app.strict_streak_guard import install_strict_streak_guard
from app.telegram_admin_integration import install_telegram_admin_integration
from app.tick_debug_logging import install_every_tick_debug_logging
from app.websocket_only_execution import install_websocket_only_execution


async def run_worker() -> None:
    # Account lifecycle remains account-scoped. Pause preserves recovery/session;
    # Stop/Start Again resets that user's state without stopping other traders.
    install_worker_account_lifecycle()

    # All production purchases remain private Deriv WebSocket-only.
    install_websocket_only_execution()

    # Keep account-level execution explanations and error diagnostics.
    install_account_execution_feedback()
    install_account_execution_diagnostics()

    # Telegram private admin/lifecycle features remain independent of strategy.
    install_telegram_admin_integration()

    # Replace the historical hard-coded deployment message with a release note
    # generated from the exact commit range installed by scripts/update_vps.sh.
    install_dynamic_deployment_announcement()

    # Build the strict PUT recovery brain first. The hybrid controller is installed
    # afterwards so PUT remains reachable only while the hybrid state is recovering
    # and still retains the established 15 -> 5 -> 1 confirmation gate.
    install_strict_streak_guard()
    install_hybrid_runtime_config()

    # Hybrid digit candidates must exist in both the generic candidate ledger and
    # directional ledger before a canonical SystemModelTrade can reference them.
    install_hybrid_data_integrity()
    install_hybrid_digit_put_strategy()

    # Primary entry uses one recent 20-digit OVER-2 bias; the old 100/500/1000 +
    # Wilson gate is not part of production entry decisions.
    install_recent_digit_bias_strategy()

    # Install hybrid safety before the final account-balance policy so the latter
    # becomes the authoritative stake planner used by the completed worker.
    install_hybrid_worker_safety()

    # Admission requires only the selected stake. No safety/recovery reserve or
    # recovery-balance cap may pre-empt a provider buy request. Deriv returns the
    # actual insufficient-funds error when a later requested stake cannot be paid.
    install_stake_only_balance_policy()

    # Install last so committed settlement notifications retry and publish again
    # after the final account-balance reconciliation has reached PostgreSQL.
    install_production_worker_integration()

    # Optional high-volume diagnostics. Disabled unless EVERY_TICK_LOGS=true.
    install_every_tick_debug_logging()

    bot = RFDir5TradingBot()
    loop = asyncio.get_running_loop()

    def stop() -> None:
        bot.is_running = False

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop)
        except NotImplementedError:
            pass

    await bot.run()


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
