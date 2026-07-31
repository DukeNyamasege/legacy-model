from __future__ import annotations

import asyncio
import base64
import binascii
import os

from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_LAST_RELEASE_KEY = "telegram_last_deployment_release_id"


def _decode_release_message(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return ""
    return decoded.strip()[:4096]


def install_dynamic_deployment_announcement() -> None:
    """Replace stale startup copy with one message for the actual deployed commit range."""
    global _INSTALLED
    if _INSTALLED:
        return

    async def send_current_deployment_announcement(self: RFDir5TradingBot) -> None:
        release_id = os.getenv("DEPLOYMENT_RELEASE_ID", "").strip()
        message = _decode_release_message(
            os.getenv("DEPLOYMENT_RELEASE_MESSAGE_B64", "")
        )
        change_count = int(os.getenv("DEPLOYMENT_RELEASE_CHANGE_COUNT", "0") or 0)

        if not release_id or not message or change_count <= 0:
            self.logger.info(
                "TELEGRAM_DEPLOYMENT_RELEASE_SKIPPED reason=no_new_deployment_changes"
            )
            return

        if self.repository.runtime_preference(_LAST_RELEASE_KEY) == release_id:
            self.logger.info(
                "TELEGRAM_DEPLOYMENT_RELEASE_SKIPPED reason=already_sent release_id=%s",
                release_id[:12],
            )
            return

        for attempt in range(1, 4):
            try:
                if await self.telegram_alerts.send_announcement(message):
                    self.repository.set_runtime_preference(
                        _LAST_RELEASE_KEY,
                        release_id,
                    )
                    self.logger.info(
                        "TELEGRAM_DEPLOYMENT_RELEASE_SENT release_id=%s changes=%s attempt=%s",
                        release_id[:12],
                        change_count,
                        attempt,
                    )
                    return
            except Exception as exc:
                self.logger.warning(
                    "TELEGRAM_DEPLOYMENT_RELEASE_ATTEMPT_FAILED release_id=%s "
                    "attempt=%s error=%s",
                    release_id[:12],
                    attempt,
                    type(exc).__name__,
                )
            if attempt < 3:
                await asyncio.sleep(float(2 ** (attempt - 1)))

        self.logger.warning(
            "TELEGRAM_DEPLOYMENT_RELEASE_FAILED release_id=%s changes=%s; "
            "the release remains unsent and will be retried on the next worker start",
            release_id[:12],
            change_count,
        )

    async def disable_legacy_virtual_announcement(
        self: RFDir5TradingBot,
    ) -> None:
        # This was a one-off historical product announcement. It must not be
        # replayed during future deployments or after a database replacement.
        self.logger.debug("TELEGRAM_LEGACY_VIRTUAL_ANNOUNCEMENT_DISABLED")

    RFDir5TradingBot._send_deploy_announcement = (
        send_current_deployment_announcement
    )
    RFDir5TradingBot._send_virtual_protection_announcement_once = (
        disable_legacy_virtual_announcement
    )
    _INSTALLED = True
