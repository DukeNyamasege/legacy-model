from __future__ import annotations

import os
from typing import Any

import aiohttp

from app.config import TelegramSettings


class TelegramAlertClient:
    def __init__(self, settings: TelegramSettings, logger: Any) -> None:
        self.settings = settings
        self.logger = logger
        self.bot_token = os.getenv(settings.bot_token_env, "").strip()
        self.chat_id = os.getenv(settings.chat_id_env, "").strip()
        self.enabled = bool(settings.enabled and self.bot_token and self.chat_id)
        if settings.enabled and not self.enabled:
            self.logger.warning(
                "TELEGRAM_ALERTS_DISABLED reason=missing_bot_token_or_chat_id"
            )

    @staticmethod
    def format_hourly_report(report: dict[str, Any]) -> str:
        return "\n".join(
            (
                "Father of Automation - hourly execution report",
                f"Window: {report['window_minutes']} minutes",
                f"Mode: {str(report['mode']).upper()}",
                f"Strategy: {report['strategy']}",
                f"Direction: {report['direction']} ({report['contract_type']})",
                f"Active accounts: {report['active_accounts']}",
                f"Excluded accounts: {report['excluded_accounts']}",
                f"Master: {report['master_account'] or 'not configured'}",
                (
                    "Master results: "
                    f"{report['master_trades']} trades, "
                    f"{report['master_wins']} wins, "
                    f"{report['master_losses']} losses, "
                    f"P/L {report['master_profit']:.2f} USD"
                ),
                (
                    "All accounts: "
                    f"{report['all_account_runs']} contracts, "
                    f"P/L {report['all_account_profit']:.2f} USD"
                ),
                f"Open contracts: {report['open_contracts']}",
                f"Generated: {report['generated_at']}",
            )
        )

    async def send_hourly_report(self, report: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    data={
                        "chat_id": self.chat_id,
                        "text": self.format_hourly_report(report),
                        "disable_web_page_preview": "true",
                    },
                ) as response:
                    if response.status != 200:
                        self.logger.warning(
                            "TELEGRAM_ALERT_FAILED status=%s",
                            response.status,
                        )
                        return False
            self.logger.info("TELEGRAM_HOURLY_ALERT_SENT")
            return True
        except (aiohttp.ClientError, TimeoutError) as exc:
            self.logger.warning(
                "TELEGRAM_ALERT_FAILED error=%s",
                type(exc).__name__,
            )
            return False
