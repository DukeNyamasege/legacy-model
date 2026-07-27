from __future__ import annotations

import asyncio
import signal

from app.account_execution_diagnostics import install_account_execution_diagnostics
from app.account_execution_feedback import install_account_execution_feedback
from app.account_lifecycle import install_worker_account_lifecycle
from app.rf_dir5_bot import RFDir5TradingBot
from app.strict_streak_guard import install_strict_streak_guard
from app.websocket_only_execution import install_websocket_only_execution


async def run_worker() -> None:
    # Install account-scoped Pause/Stop semantics before the bot instance is
    # created. Pause preserves recovery/session state; Stop/Start Again resets it.
    install_worker_account_lifecycle()

    # Production execution is deliberately WebSocket-only. This guard is
    # installed before the bot instance is created so REST Bulk Purchase cannot
    # become active because of a configuration change or future refactor.
    install_websocket_only_execution()

    # Persist every account-level execution failure and protect small accounts
    # from inheriting/attempting oversized recovery stakes. This wraps the
    # WebSocket-only transport, so it cannot re-enable REST execution.
    install_account_execution_feedback()

    # Surface transient eligibility, contract verification and registration
    # failures instead of leaving a joined account at 0 trades with no reason.
    install_account_execution_diagnostics()

    # Keep the existing RF-PUT5 five-tick brain, but require broader 15-tick
    # directional agreement and one confirming tick before execution. After one
    # canonical loss the next setup must meet stronger score/efficiency context;
    # the existing per-account 2-loss virtual protection remains the hard break.
    install_strict_streak_guard()

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
