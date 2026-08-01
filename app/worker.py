from __future__ import annotations

import asyncio
import signal

from app.account_execution_diagnostics import install_account_execution_diagnostics
from app.account_execution_feedback import install_account_execution_feedback
from app.account_lifecycle import install_worker_account_lifecycle
from app.account_mode_execution_lock import install_account_mode_execution_lock
from app.account_reenrollment import install_account_reenrollment
from app.ai_digit_recovery_v1 import install_ai_digit_recovery_v1_strategy
from app.custom_martingale import install_custom_martingale_worker
from app.dashboard_actual_trade_fallback import install_dashboard_actual_trade_fallback
from app.deployment_announcement import install_dynamic_deployment_announcement
from app.hybrid_data_integrity import install_hybrid_data_integrity
from app.hybrid_digit_put import install_hybrid_digit_put_strategy
from app.hybrid_runtime_config import install_hybrid_runtime_config
from app.private_buy_parameter_hardening import install_private_buy_parameter_hardening
from app.private_websocket_rate_limit import install_private_websocket_rate_limit
from app.production_worker_integration import install_production_worker_integration
from app.profit_accuracy_guard import install_profit_accuracy_guard
from app.real_demo_trading_support import install_dual_demo_real_trading_support
from app.rf_dir5_bot import RFDir5TradingBot
from app.stake_only_balance_policy import install_stake_only_balance_policy
from app.strict_streak_guard import install_strict_streak_guard
from app.telegram_admin_integration import install_telegram_admin_integration
from app.tick_debug_logging import install_every_tick_debug_logging
from app.websocket_only_execution import install_websocket_only_execution


async def run_worker() -> None:
    # Only the current enrollment generation is visible to the worker. Historical
    # registrations remain preserved but cannot auto-start after a reset.
    install_account_reenrollment()

    install_dashboard_actual_trade_fallback()
    install_worker_account_lifecycle()
    install_account_mode_execution_lock()
    install_dual_demo_real_trading_support()
    install_websocket_only_execution()
    install_private_buy_parameter_hardening()
    install_private_websocket_rate_limit()
    install_account_execution_feedback()
    install_account_execution_diagnostics()
    install_telegram_admin_integration()
    install_dynamic_deployment_announcement()

    # Build the old RF/hybrid envelope only for shared WebSocket, candidate,
    # account and settlement infrastructure. The active strategy is installed
    # afterwards and disables all PUT scheduling.
    install_strict_streak_guard()
    install_hybrid_runtime_config()
    install_hybrid_data_integrity()
    install_hybrid_digit_put_strategy()

    install_profit_accuracy_guard()
    install_stake_only_balance_policy()
    install_custom_martingale_worker()

    # Active public-release strategy:
    # NORMAL   -> DIGITOVER 1
    # RECOVERY -> DIGITOVER 3
    # VIRTUAL  -> virtual DIGITOVER 3 until 2 consecutive wins
    # SPLIT    -> real DIGITOVER 3 in two recovery-profit targets
    install_ai_digit_recovery_v1_strategy()

    install_production_worker_integration()
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
