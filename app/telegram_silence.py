from __future__ import annotations

import logging
import os
from typing import Any

_INSTALLED = False


def telegram_notifications_suspended() -> bool:
    """Return the operator-controlled global Telegram kill-switch state."""

    raw = os.getenv("TELEGRAM_NOTIFICATIONS_SUSPENDED", "false")
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def install_telegram_silence() -> None:
    """Block every channel and private Telegram send while suspension is active.

    The patch is process-local and reversible: set
    TELEGRAM_NOTIFICATIONS_SUSPENDED=false and restart API/worker to restore the
    normal channel and private-admin behaviour.
    """

    global _INSTALLED
    if _INSTALLED or not telegram_notifications_suspended():
        return

    from app.services import telegram_admin
    from app.services.telegram_alerts import TelegramAlertClient

    logger = logging.getLogger("legacy_model.telegram_silence")
    original_init = TelegramAlertClient.__init__

    def silent_init(self: TelegramAlertClient, settings: Any, client_logger: Any) -> None:
        original_init(self, settings, client_logger)
        self.enabled = False
        client_logger.warning(
            "TELEGRAM_NOTIFICATIONS_SUSPENDED channel=false private=false polling=false"
        )

    async def no_send(*_args: Any, **_kwargs: Any) -> bool:
        return False

    async def no_poll(*_args: Any, **_kwargs: Any) -> None:
        return None

    def no_private_sync(*_args: Any, **_kwargs: Any) -> bool:
        return False

    TelegramAlertClient.__init__ = silent_init
    TelegramAlertClient.discover_channel = no_send
    TelegramAlertClient._send_text = no_send
    TelegramAlertClient._send_photo = no_send
    TelegramAlertClient.send_hourly_report = no_send
    TelegramAlertClient.send_announcement = no_send

    # These globals are resolved by the already-imported queue functions at call
    # time, so replacing them also silences lifecycle/status threads that other
    # modules imported before this installer ran.
    telegram_admin._bot_token = lambda _settings: ""
    telegram_admin._send_private_sync = no_private_sync
    telegram_admin.TelegramAdminController._send_private = no_send
    telegram_admin.TelegramAdminController.run = no_poll

    logger.warning(
        "TELEGRAM_GLOBAL_KILL_SWITCH_ACTIVE no_messages_will_be_sent_until_reenabled"
    )
    _INSTALLED = True
