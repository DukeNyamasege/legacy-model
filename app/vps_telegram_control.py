from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable

import aiohttp
from sqlalchemy import select

import app.api as base_api
from app.models import RuntimePreference
from app.services.telegram_admin import (
    ADMIN_CHAT_KEY,
    ADMIN_OFFSET_KEY,
    ADMIN_USERNAME_KEY,
    TelegramAdminController,
    _normalise_username,
)
from app.services.telegram_alerts import TelegramAlertClient
from app.telegram_silence import telegram_notifications_suspended


LOGGER = logging.getLogger("legacy_model.vps_telegram_control")
_INSTALLED = False
SUBSCRIBER_PREFIX = "telegram_broadcast_subscriber:"
CHANNEL_CHAT_KEY = "telegram_channel_chat_id"
CHANNEL_TITLE_KEY = "telegram_channel_title"


def _subscriber_key(chat_id: str) -> str:
    return f"{SUBSCRIBER_PREFIX}{str(chat_id).strip()}"


def _subscriber_payloads() -> list[dict[str, Any]]:
    with base_api.DATABASE.session() as session:
        rows = list(
            session.scalars(
                select(RuntimePreference)
                .where(RuntimePreference.preference_key.like(f"{SUBSCRIBER_PREFIX}%"))
                .order_by(RuntimePreference.preference_key)
            ).all()
        )
    result: list[dict[str, Any]] = []
    for row in rows:
        raw = str(row.preference_value or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        chat_id = str(payload.get("chat_id") or "").strip()
        if chat_id:
            result.append(payload)
    return result


def _store_subscriber(chat: dict[str, Any], sender: dict[str, Any]) -> None:
    chat_id = str(chat.get("id") or "").strip()
    if not chat_id:
        return
    payload = {
        "chat_id": chat_id,
        "username": _normalise_username(sender.get("username") or chat.get("username") or ""),
        "first_name": str(sender.get("first_name") or "").strip(),
        "subscribed_at": datetime.now(timezone.utc).isoformat(),
    }
    base_api.REPOSITORY.set_runtime_preference(
        _subscriber_key(chat_id),
        json.dumps(payload, separators=(",", ":")),
    )


def _remove_subscriber(chat_id: str) -> None:
    base_api.REPOSITORY.set_runtime_preference(_subscriber_key(chat_id), "")


def _seed_channel(client: TelegramAlertClient) -> None:
    if client.chat_id:
        base_api.REPOSITORY.set_runtime_preference(CHANNEL_CHAT_KEY, client.chat_id)
        if client.chat_title:
            base_api.REPOSITORY.set_runtime_preference(CHANNEL_TITLE_KEY, client.chat_title)
        return
    stored_chat = base_api.REPOSITORY.runtime_preference(CHANNEL_CHAT_KEY).strip()
    if not stored_chat:
        return
    client.chat_id = stored_chat
    client.chat_title = base_api.REPOSITORY.runtime_preference(CHANNEL_TITLE_KEY).strip()
    client._cache_channel()


def _remember_channel(client: TelegramAlertClient, chat: dict[str, Any]) -> None:
    if str(chat.get("type") or "") != "channel":
        return
    chat_id = str(chat.get("id") or "").strip()
    if not chat_id:
        return
    client.chat_id = chat_id
    client.chat_title = str(chat.get("title") or "").strip()
    base_api.REPOSITORY.set_runtime_preference(CHANNEL_CHAT_KEY, chat_id)
    base_api.REPOSITORY.set_runtime_preference(CHANNEL_TITLE_KEY, client.chat_title)
    client._cache_channel()
    LOGGER.info("TELEGRAM_CHANNEL_REMEMBERED title=%s", client.chat_title or "channel")


class VpsTelegramController(TelegramAdminController):
    """Single VPS Telegram poller for private admin control and opt-in broadcasts."""

    def _is_admin(self, chat_id: str, username: str) -> bool:
        stored_chat = base_api.REPOSITORY.runtime_preference(ADMIN_CHAT_KEY).strip()
        if stored_chat and str(chat_id).strip() == stored_chat:
            return True
        return bool(username and username == self.admin_username)

    def _help_text(self) -> str:
        return super()._help_text() + "\n" + "\n".join(
            (
                "",
                "FULL-VPS TELEGRAM COMMANDS",
                "/update message — post once to the channel + DM all opted-in bot subscribers",
                "/broadcast message — DM opted-in bot subscribers only",
                "/publish message — publish to the saved Telegram channel only",
                "/subscribers — show direct-alert subscriber count",
                "",
                "Channel subscribers cannot be force-mentioned as @all/@everyone by the Telegram Bot API.",
                "Users who open this bot and send /start become eligible for direct /update broadcasts.",
            )
        )

    async def _publish_known_channel(self, text: str) -> bool:
        if not self.channel_client.chat_id:
            return False
        return await self.channel_client.send_announcement(text)

    async def _broadcast_subscribers(
        self,
        text: str,
        *,
        exclude_chat_id: str = "",
    ) -> tuple[int, int]:
        sent = 0
        failed = 0
        for item in _subscriber_payloads():
            chat_id = str(item.get("chat_id") or "").strip()
            if not chat_id or chat_id == str(exclude_chat_id or "").strip():
                continue
            if await self._send_private(chat_id, text):
                sent += 1
            else:
                failed += 1
            # Stay below Telegram's ordinary bulk delivery ceiling.
            await asyncio.sleep(0.05)
        return sent, failed

    async def _handle_admin_message(self, chat_id: str, text: str) -> None:
        raw = str(text or "").strip()
        lower = raw.lower()

        if lower == "/subscribers":
            await self._send_private(
                chat_id,
                f"👥 Direct-alert subscribers: {len(_subscriber_payloads())}",
            )
            return

        if lower == "/publish-status":
            sent = await self._publish_known_channel(self._status_text())
            await self._send_private(
                chat_id,
                "✅ Current VPS status was published to the channel."
                if sent
                else "❌ No saved channel or channel publication failed.",
            )
            return

        if lower.startswith("/publish "):
            message = raw.split(maxsplit=1)[1].strip()
            sent = bool(message) and await self._publish_known_channel(message)
            await self._send_private(
                chat_id,
                "✅ Message published to the channel."
                if sent
                else "❌ No saved channel or channel publication failed.",
            )
            return

        if lower.startswith("/broadcast "):
            message = raw.split(maxsplit=1)[1].strip()
            if not message:
                await self._send_private(chat_id, "Use: /broadcast your message")
                return
            sent, failed = await self._broadcast_subscribers(message, exclude_chat_id=chat_id)
            await self._send_private(
                chat_id,
                f"✅ Direct broadcast complete. Sent: {sent} | Failed: {failed}",
            )
            return

        if lower.startswith("/update "):
            message = raw.split(maxsplit=1)[1].strip()
            if not message:
                await self._send_private(chat_id, "Use: /update your message")
                return
            channel_sent = await self._publish_known_channel(message)
            direct_sent, direct_failed = await self._broadcast_subscribers(
                message,
                exclude_chat_id=chat_id,
            )
            await self._send_private(
                chat_id,
                "\n".join(
                    (
                        "📣 ONE-UPDATE DELIVERY COMPLETE",
                        f"Channel: {'SENT' if channel_sent else 'NOT SENT'}",
                        f"Direct subscriber inboxes: {direct_sent}",
                        f"Direct failures: {direct_failed}",
                    )
                ),
            )
            return

        await super()._handle_admin_message(chat_id, raw)

    async def run(self, is_running: Callable[[], bool]) -> None:
        if not self.bot_token:
            LOGGER.warning("VPS_TELEGRAM_CONTROL_DISABLED reason=missing_bot_token")
            return

        stored_offset = self.repository.runtime_preference(ADMIN_OFFSET_KEY).strip()
        try:
            offset = int(stored_offset or 0)
        except ValueError:
            offset = 0
        timeout = aiohttp.ClientTimeout(total=35)

        while is_running():
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        f"https://api.telegram.org/bot{self.bot_token}/getUpdates",
                        params={
                            "offset": offset,
                            "limit": 100,
                            "timeout": 25,
                            "allowed_updates": json.dumps(
                                [
                                    "message",
                                    "channel_post",
                                    "edited_channel_post",
                                    "my_chat_member",
                                ]
                            ),
                        },
                    ) as response:
                        payload = await response.json(content_type=None)
                if response.status != 200 or not payload.get("ok"):
                    LOGGER.warning(
                        "VPS_TELEGRAM_POLL_FAILED status=%s reason=%s",
                        response.status,
                        str(payload.get("description") or "unknown")[:160],
                    )
                    await asyncio.sleep(5)
                    continue

                for update in payload.get("result") or []:
                    update_id = int(update.get("update_id") or 0)
                    offset = max(offset, update_id + 1)
                    self.repository.set_runtime_preference(ADMIN_OFFSET_KEY, str(offset))

                    channel_event = (
                        update.get("channel_post")
                        or update.get("edited_channel_post")
                        or update.get("my_chat_member")
                    )
                    if isinstance(channel_event, dict):
                        chat = channel_event.get("chat") or {}
                        if isinstance(chat, dict):
                            _remember_channel(self.channel_client, chat)

                    message = update.get("message")
                    if not isinstance(message, dict):
                        continue
                    chat = message.get("chat") or {}
                    sender = message.get("from") or {}
                    if str(chat.get("type") or "") != "private":
                        continue
                    chat_id = str(chat.get("id") or "").strip()
                    if not chat_id:
                        continue
                    username = _normalise_username(
                        sender.get("username") or chat.get("username") or ""
                    )
                    message_text = str(message.get("text") or "").strip()

                    if self._is_admin(chat_id, username):
                        self.repository.set_runtime_preference(ADMIN_CHAT_KEY, chat_id)
                        self.repository.set_runtime_preference(ADMIN_USERNAME_KEY, username)
                        await self._handle_admin_message(chat_id, message_text)
                        continue

                    lower = message_text.lower()
                    if lower.startswith("/start") or lower in {"/subscribe", "subscribe"}:
                        _store_subscriber(chat, sender)
                        await self._send_private(
                            chat_id,
                            "✅ Direct Model Updater alerts enabled. You can stop them any time with /unsubscribe.",
                        )
                    elif lower in {"/unsubscribe", "unsubscribe", "/stop"}:
                        _remove_subscriber(chat_id)
                        await self._send_private(
                            chat_id,
                            "🔕 Direct Model Updater alerts disabled.",
                        )
                    else:
                        await self._send_private(
                            chat_id,
                            "Send /start to receive direct Model Updater announcements, or /unsubscribe to stop them.",
                        )
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                LOGGER.warning("VPS_TELEGRAM_POLL_FAILED error=%s", type(exc).__name__)
                await asyncio.sleep(5)


def install_vps_telegram_control(app: Any) -> None:
    """Install the single full-VPS Telegram polling/control plane on the API."""

    global _INSTALLED
    if _INSTALLED:
        return

    channel_client = TelegramAlertClient(base_api.CONFIG.telegram, LOGGER)
    _seed_channel(channel_client)
    controller = VpsTelegramController(
        base_api.REPOSITORY,
        base_api.CONFIG,
        LOGGER,
        channel_client,
    )
    running = {"value": True}

    async def startup() -> None:
        if telegram_notifications_suspended():
            LOGGER.warning("VPS_TELEGRAM_CONTROL_DISABLED reason=notifications_suspended")
            return
        if not controller.bot_token:
            LOGGER.warning("VPS_TELEGRAM_CONTROL_DISABLED reason=missing_bot_token")
            return
        existing = getattr(app.state, "vps_telegram_control_task", None)
        if existing is not None and not existing.done():
            return
        running["value"] = True
        app.state.vps_telegram_control_task = asyncio.create_task(
            controller.run(lambda: bool(running["value"])),
            name="vps_telegram_control",
        )
        LOGGER.warning(
            "VPS_TELEGRAM_CONTROL_ACTIVE private_admin_autodiscovery=true "
            "channel_updates=true opt_in_broadcast=true one_update_command=/update "
            "force_mention_all=false lifecycle=lifespan"
        )

    async def shutdown() -> None:
        running["value"] = False
        task = getattr(app.state, "vps_telegram_control_task", None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            app.state.vps_telegram_control_task = None

    # Current Starlette removed add_event_handler()/on_event(). Compose our
    # lifecycle around the lifespan already owned by the fully-built FastAPI app.
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def telegram_lifespan(lifespan_app: Any):
        async with previous_lifespan(lifespan_app) as state:
            await startup()
            try:
                yield state
            finally:
                await shutdown()

    app.router.lifespan_context = telegram_lifespan
    app.state.vps_telegram_control_installed = True
    app.state.vps_telegram_channel_client = channel_client
    _INSTALLED = True
