from __future__ import annotations

import asyncio
import signal

from app.account_lifecycle import install_worker_account_lifecycle
from app.rf_dir5_bot import RFDir5TradingBot
from app.websocket_only_execution import install_websocket_only_execution


async def run_worker() -> None:
    # Install account-scoped Pause/Stop semantics before the bot instance is
    # created. Pause preserves recovery/session state; Stop/Start Again resets it.
    install_worker_account_lifecycle()

    # Production execution is deliberately WebSocket-only. This guard is
    # installed before the bot instance is created so REST Bulk Purchase cannot
    # become active because of a configuration change or future refactor.
    install_websocket_only_execution()

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
