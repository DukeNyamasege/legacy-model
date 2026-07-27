from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.models import CandidateSignalRecord
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
    original_handle_admin_message = TelegramAdminController._handle_admin_message

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

    def why_latest_trade_text(self: TelegramAdminController) -> str:
        with self.repository.database.session() as session:
            signal = session.scalar(
                select(CandidateSignalRecord)
                .where(CandidateSignalRecord.run_id == self.repository.run_id)
                .order_by(CandidateSignalRecord.generated_timestamp.desc())
                .limit(1)
            )
        if signal is None:
            return "No model candidate has been recorded yet, so there is no missed trade to explain."

        final_status = str(signal.final_status or "CREATED")
        expected = list(signal.expected_account_masks or [])
        registered = list(signal.registered_account_masks or [])
        missing = sorted(set(expected) - set(registered))
        lines = [
            "🔎 WHY WAS THE LATEST TRADE NOT PURCHASED?",
            "",
            f"Signal: {signal.signal_id}",
            f"Market: {signal.symbol}",
            f"Contract: {signal.contract_type}",
            f"Model status: {final_status}",
            f"Expected accounts: {len(expected)}",
            f"Purchased/registered accounts: {len(registered)}",
        ]
        if missing:
            lines.append(f"Accounts that missed it: {', '.join(missing[:10])}")
        if final_status.startswith("SKIP"):
            lines.append(
                "Meaning: the model itself rejected/skipped this candidate before a purchase was allowed."
            )
        elif final_status in {"PURCHASE_FAILED", "PURCHASE_PARTIAL"}:
            lines.append(
                "Meaning: the model selected the trade, but one or more account-level purchases did not complete."
            )
        elif final_status == "PURCHASE_CONFIRMED":
            lines.append("Meaning: the model purchase was confirmed for its registered accounts.")
        else:
            lines.append("Meaning: this is the latest recorded model execution state.")

        real_issues = [
            item
            for item in self._real_accounts()
            if item["execution_status"].lower() not in {"active", "connecting", "validating"}
        ]
        if real_issues:
            lines.extend(("", "Current REAL-account blockers:"))
            for item in real_issues[:8]:
                lines.append(
                    f"{item['masked']} — {item['execution_status']}: "
                    f"{item['execution_status_reason'] or 'No reason recorded'}"
                )
        return "\n".join(lines)

    async def handle_admin_message_with_natural_queries(
        self: TelegramAdminController,
        chat_id: str,
        text: str,
    ) -> None:
        raw = str(text or "").strip()
        lower = raw.lower()
        missed_trade_question = (
            "why" in lower
            and any(word in lower for word in ("trade", "purchase", "purchased", "contract"))
            and not lower.startswith("/why ")
        )
        progress_question = any(
            phrase in lower
            for phrase in (
                "what is happening",
                "what's happening",
                "give me progress",
                "progress of the bot",
                "bot progress",
                "how is the bot doing",
                "how is my bot doing",
            )
        )
        if missed_trade_question:
            await self._send_private(chat_id, why_latest_trade_text(self))
            return
        if progress_question:
            await self._send_private(chat_id, self._status_text())
            return
        await original_handle_admin_message(self, chat_id, raw)

    async def run_with_admin_control(self: RFDir5TradingBot) -> None:
        controller = TelegramAdminController(
            self.repository,
            self.test2_config,
            self.logger,
            self.telegram_alerts,
        )

        async def delayed_private_admin() -> None:
            # The existing channel publisher may do one immediate getUpdates call
            # to discover its channel after boot. Let that finish first, then keep
            # one long-poll consumer for private admin messages and future channel
            # discovery. This avoids Telegram 409 getUpdates conflicts at startup.
            await asyncio.sleep(2)
            await controller.run(lambda: bool(self.is_running))

        admin_task = asyncio.create_task(
            delayed_private_admin(),
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
    TelegramAdminController._handle_admin_message = handle_admin_message_with_natural_queries
    RFDir5TradingBot.run = run_with_admin_control
    _INSTALLED = True
