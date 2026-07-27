from __future__ import annotations

import asyncio
import logging

from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot
from app.services.telegram_admin import TelegramAdminController, queue_real_status_alert


_INSTALLED = False


def install_telegram_admin_integration() -> None:
    """Install the private Telegram admin control plane into the production worker."""
    global _INSTALLED
    if _INSTALLED:
        return

    logger = logging.getLogger("legacy_model.telegram_admin")
    original_set_status = Test2Repository.set_managed_account_execution_status
    original_run = RFDir5TradingBot.run

    def set_status_with_real_admin_alert(
        self: Test2Repository,
        account_id: int,
        execution_status: str,
        reason: str = "",
    ) -> None:
        original_set_status(self, account_id, execution_status, reason)
        queue_real_status_alert(
            self,
            self.config,
            logger,
            managed_account_id=int(account_id),
            status=str(execution_status or ""),
            reason=str(reason or ""),
        )

    # Use the repository's joined signal/trade view so /lasttrade reports the
    # actual market without assuming a non-existent Trade.market column.
    def last_trade_text(self: TelegramAdminController) -> str:
        rows = self.repository.recent_trades(limit=1)
        if not rows:
            return "No trade has been recorded yet."
        trade = rows[0]
        return "\n".join(
            (
                "🧾 LAST RECORDED TRADE",
                "",
                f"Account: {trade.get('account') or 'unknown'}",
                f"Market: {trade.get('symbol') or 'unknown'}",
                f"Contract: {trade.get('contract_type') or 'unknown'}",
                f"Stake: {float(trade.get('buy_price') or 0):.2f} USD",
                f"Outcome: {trade.get('outcome') or 'OPEN'}",
                f"Profit: {float(trade.get('profit') or 0):+.2f} USD",
                f"Purchased: {trade.get('purchase_time') or 'unknown'}",
            )
        )

    async def run_with_admin_control(self: RFDir5TradingBot) -> None:
        controller = TelegramAdminController(
            self.repository,
            self.test2_config,
            self.logger,
            self.telegram_alerts,
        )
        admin_task = asyncio.create_task(
            controller.run(lambda: bool(self.is_running)),
            name="telegram_private_admin",
        )
        try:
            await original_run(self)
        finally:
            admin_task.cancel()
            try:
                await admin_task
            except asyncio.CancelledError:
                pass

    Test2Repository.set_managed_account_execution_status = set_status_with_real_admin_alert
    TelegramAdminController._last_trade_text = last_trade_text
    RFDir5TradingBot.run = run_with_admin_control
    _INSTALLED = True
