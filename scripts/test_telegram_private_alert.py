from __future__ import annotations

import logging

import app.api as base_api
from app.services import telegram_admin
from app.telegram_silence import telegram_notifications_suspended


def main() -> None:
    token_ready = bool(telegram_admin._bot_token(base_api.CONFIG.telegram))
    chat_ready = bool(telegram_admin._admin_chat_id(base_api.REPOSITORY))
    suspended = bool(telegram_notifications_suspended())

    print(f"telegram_bot_token_configured={str(token_ready).lower()}")
    print(f"telegram_admin_chat_configured={str(chat_ready).lower()}")
    print(f"telegram_notifications_suspended={str(suspended).lower()}")

    if not token_ready or not chat_ready or suspended:
        print("TELEGRAM_PRIVATE_ALERT_TEST_NOT_SENT")
        raise SystemExit(2)

    logger = logging.getLogger("legacy_model.telegram_private_test")
    sent = telegram_admin._send_private_sync(
        base_api.REPOSITORY,
        base_api.CONFIG.telegram,
        logger,
        (
            "✅ DERIVADMIN PRIVATE ALERTS READY\n\n"
            "Fresh user login alerts and Auto Trade start alerts are enabled."
        ),
    )
    print("TELEGRAM_PRIVATE_ALERT_TEST_SENT" if sent else "TELEGRAM_PRIVATE_ALERT_TEST_FAILED")
    raise SystemExit(0 if sent else 1)


if __name__ == "__main__":
    main()
