from __future__ import annotations

import asyncio
import signal

from app.account_execution_diagnostics import install_account_execution_diagnostics
from app.account_execution_feedback import install_account_execution_feedback
from app.account_lifecycle import install_worker_account_lifecycle
from app.hybrid_digit_put import install_hybrid_digit_put_strategy
from app.hybrid_runtime_config import install_hybrid_runtime_config
from app.rf_dir5_bot import RFDir5TradingBot
from app.strict_streak_guard import install_strict_streak_guard
from app.telegram_admin_integration import install_telegram_admin_integration
from app.websocket_only_execution import install_websocket_only_execution


async def run_worker() -> None:
    # Account lifecycle remains account-scoped. Pause preserves recovery/session;
    # Stop/Start Again resets that user's state without stopping other traders.
    install_worker_account_lifecycle()

    # All production purchases remain private Deriv WebSocket-only.
    install_websocket_only_execution()

    # Keep account-level execution explanations and small-account recovery safety.
    install_account_execution_feedback()
    install_account_execution_diagnostics()

    # Telegram private admin/lifecycle features remain independent of strategy.
    install_telegram_admin_integration()

    # Build the existing strict PUT recovery brain first. The hybrid controller is
    # deliberately installed afterwards so PUT is reachable only when the hybrid
    # state is recovering and still retains the 15 -> 5 -> 1 confirmation gate.
    install_strict_streak_guard()
    install_hybrid_runtime_config()
    install_hybrid_digit_put_strategy()

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
