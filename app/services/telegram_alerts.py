from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import aiohttp

from app.config import TelegramSettings
from app.services.dashboard_screenshot import DashboardScreenshotCapture


class TelegramAlertClient:
    def __init__(self, settings: TelegramSettings, logger: Any) -> None:
        self.settings = settings
        self.logger = logger
        self.bot_token = os.getenv(settings.bot_token_env, "").strip()
        self.chat_id = os.getenv(settings.chat_id_env, "").strip()
        self.chat_title = ""
        self.channel_cache_path = Path(settings.channel_cache_path)
        self.dashboard_capture = DashboardScreenshotCapture(settings, logger)
        if not self.chat_id:
            self._load_cached_channel()
        self.enabled = bool(settings.enabled and self.bot_token)
        if settings.enabled and not self.enabled:
            self.logger.warning("TELEGRAM_ALERTS_DISABLED reason=missing_bot_token")
        elif self.enabled and not self.chat_id:
            self.logger.info(
                "TELEGRAM_CHANNEL_DISCOVERY_PENDING method=getUpdates "
                "action=publish_one_channel_post_if_discovery_waits"
            )

    def _load_cached_channel(self) -> None:
        try:
            payload = json.loads(self.channel_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        self.chat_id = str(payload.get("chat_id") or "").strip()
        self.chat_title = str(payload.get("title") or "").strip()

    def _cache_channel(self) -> None:
        try:
            self.channel_cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.channel_cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {"chat_id": self.chat_id, "title": self.chat_title},
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            temporary.replace(self.channel_cache_path)
        except OSError as exc:
            self.logger.warning(
                "TELEGRAM_CHANNEL_CACHE_FAILED error=%s",
                type(exc).__name__,
            )

    @staticmethod
    def channel_from_updates(payload: dict[str, Any]) -> tuple[str, str]:
        updates = payload.get("result")
        if not isinstance(updates, list):
            return "", ""
        for update in reversed(updates):
            if not isinstance(update, dict):
                continue
            event = (
                update.get("channel_post")
                or update.get("edited_channel_post")
                or update.get("my_chat_member")
            )
            if not isinstance(event, dict):
                continue
            chat = event.get("chat")
            if not isinstance(chat, dict) or str(chat.get("type")) != "channel":
                continue
            chat_id = str(chat.get("id") or "").strip()
            if chat_id:
                return chat_id, str(chat.get("title") or "").strip()
        return "", ""

    async def discover_channel(self) -> bool:
        if not self.enabled or self.chat_id:
            return bool(self.chat_id)
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    params={
                        "offset": -100,
                        "limit": 100,
                        "timeout": 0,
                        "allowed_updates": json.dumps(
                            [
                                "channel_post",
                                "edited_channel_post",
                                "my_chat_member",
                            ]
                        ),
                    },
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status != 200 or not payload.get("ok"):
                        self.logger.warning(
                            "TELEGRAM_CHANNEL_DISCOVERY_FAILED status=%s reason=%s",
                            response.status,
                            str(payload.get("description") or "unknown")[:160],
                        )
                        return False
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            self.logger.warning(
                "TELEGRAM_CHANNEL_DISCOVERY_FAILED error=%s",
                type(exc).__name__,
            )
            return False

        self.chat_id, self.chat_title = self.channel_from_updates(payload)
        if not self.chat_id:
            self.logger.warning(
                "TELEGRAM_CHANNEL_NOT_DISCOVERED action=publish_one_new_channel_post"
            )
            return False
        self._cache_channel()
        self.logger.info(
            "TELEGRAM_CHANNEL_DISCOVERED title=%s",
            self.chat_title or "channel",
        )
        return True

    @staticmethod
    def format_hourly_report(report: dict[str, Any]) -> str:
        return "\n".join(
            (
                "Test our model: https://derivadmin.site/",
                f"Total trades: {report['master_trades']}",
                f"Trade type: {report['direction']} ({report['contract_type']})",
                f"Per-account profit: {report['master_profit']:.2f} USD",
                f"Total profit: {report['all_account_profit']:.2f} USD",
                (
                    f"Consecutive wins/losses: {report['consecutive_wins']}"
                    f"/{report['consecutive_losses']}"
                ),
                "Join other traders and let's train the future.",
            )
        )

    async def _send_text(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    data={
                        "chat_id": self.chat_id,
                        "text": text,
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

    async def _send_photo(self, photo: bytes, caption: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
        form = aiohttp.FormData()
        form.add_field("chat_id", self.chat_id)
        form.add_field("caption", caption)
        form.add_field(
            "photo",
            photo,
            filename="global-dashboard.png",
            content_type="image/png",
        )
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=form) as response:
                    if response.status != 200:
                        self.logger.warning(
                            "TELEGRAM_DASHBOARD_ALERT_FAILED status=%s",
                            response.status,
                        )
                        return False
            self.logger.info("TELEGRAM_DASHBOARD_ALERT_SENT")
            return True
        except (aiohttp.ClientError, TimeoutError) as exc:
            self.logger.warning(
                "TELEGRAM_DASHBOARD_ALERT_FAILED error=%s",
                type(exc).__name__,
            )
            return False

    async def send_hourly_report(self, report: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        if not self.chat_id and not await self.discover_channel():
            return False

        text = self.format_hourly_report(report)
        screenshot = await self.dashboard_capture.capture()
        if screenshot and await self._send_photo(screenshot, text):
            return True
        if screenshot:
            self.logger.warning(
                "TELEGRAM_DASHBOARD_ALERT_FALLBACK mode=text"
            )
        return await self._send_text(text)
