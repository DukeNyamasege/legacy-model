from __future__ import annotations

import json
import logging
from typing import Any, Callable

import requests

from .config import GuardianConfig
from .security import redact

LOGGER = logging.getLogger("legacy_model.guardian.telegram")


class GuardianTelegram:
    """Private Telegram transport restricted to one numeric chat ID."""

    def __init__(self, config: GuardianConfig) -> None:
        self.config = config
        self.base_url = (
            f"https://api.telegram.org/bot{config.telegram_bot_token}"
        )
        self.admin_chat_id = str(config.telegram_admin_chat_id)
        self.offset = 0

    def _post(self, method: str, data: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/{method}",
            data=data,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("description") or "Telegram request failed"))
        return payload

    def send_text(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> int:
        data: dict[str, Any] = {
            "chat_id": self.admin_chat_id,
            "text": redact(text, maximum_chars=4096),
            "disable_web_page_preview": "true",
        }
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, separators=(",", ":"))
        payload = self._post("sendMessage", data)
        return int(payload["result"]["message_id"])

    def send_incident(self, incident_id: int, analysis: dict[str, Any], evidence: str) -> int:
        severity = str(analysis.get("severity") or "warning").upper()
        confidence = float(analysis.get("confidence") or 0.0)
        needs_code = bool(analysis.get("needs_code_change"))
        paths = [str(path) for path in analysis.get("candidate_paths") or []][:8]
        verification = [str(item) for item in analysis.get("verification") or []][:6]
        lines = [
            f"🛡 LEGACY MODEL GUARDIAN — {severity}",
            "",
            f"Incident #{incident_id}: {analysis.get('title') or 'Production incident'}",
            f"Confidence: {confidence:.0%}",
            f"Code change proposed: {'YES' if needs_code else 'NO'}",
            "",
            "What happened:",
            str(analysis.get("summary") or "No summary available."),
            "",
            "Probable cause:",
            str(analysis.get("root_cause") or "Not confirmed."),
            "",
            "Recommended action:",
            str(analysis.get("proposed_fix") or "Review manually."),
        ]
        if paths:
            lines.extend(("", "Likely files:", *[f"• {path}" for path in paths]))
        if verification:
            lines.extend(("", "Verification:", *[f"• {item}" for item in verification]))
        strategy = str(analysis.get("strategy_advice") or "").strip()
        if strategy:
            lines.extend(("", "Strategy observation:", strategy))
        evidence_preview = "\n".join(str(evidence).splitlines()[-12:])
        if evidence_preview:
            lines.extend(("", "Evidence preview:", evidence_preview[:900]))

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Approve fix" if needs_code else "✅ Acknowledge",
                        "callback_data": f"guardian:approve:{incident_id}",
                    },
                    {
                        "text": "❌ Reject",
                        "callback_data": f"guardian:reject:{incident_id}",
                    },
                ],
                [
                    {
                        "text": "📋 Details",
                        "callback_data": f"guardian:details:{incident_id}",
                    }
                ],
            ]
        }
        return self.send_text("\n".join(lines), reply_markup=keyboard)

    def edit_status(self, message_id: int, text: str) -> None:
        try:
            self._post(
                "editMessageText",
                {
                    "chat_id": self.admin_chat_id,
                    "message_id": int(message_id),
                    "text": redact(text, maximum_chars=4096),
                    "disable_web_page_preview": "true",
                },
            )
        except Exception:
            LOGGER.exception("GUARDIAN_TELEGRAM_EDIT_FAILED message_id=%s", message_id)

    def answer_callback(self, callback_id: str, text: str) -> None:
        try:
            self._post(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": str(text)[:180],
                    "show_alert": "false",
                },
                timeout=15,
            )
        except Exception:
            LOGGER.exception("GUARDIAN_CALLBACK_ANSWER_FAILED")

    def poll(self, handler: Callable[[str, int, int, str], None]) -> None:
        response = requests.get(
            f"{self.base_url}/getUpdates",
            params={
                "offset": self.offset,
                "limit": 50,
                "timeout": 25,
                "allowed_updates": json.dumps(["callback_query", "message"]),
            },
            timeout=35,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("description") or "Telegram polling failed"))

        for update in payload.get("result") or []:
            update_id = int(update.get("update_id") or 0)
            self.offset = max(self.offset, update_id + 1)

            callback = update.get("callback_query")
            if isinstance(callback, dict):
                message = callback.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id") or "")
                if chat_id != self.admin_chat_id:
                    LOGGER.warning("GUARDIAN_UNAUTHORIZED_CALLBACK chat_id=%s", chat_id)
                    continue
                handler(
                    str(callback.get("data") or ""),
                    int(message.get("message_id") or 0),
                    int(update_id),
                    str(callback.get("id") or ""),
                )
                continue

            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id") or "")
            if chat_id != self.admin_chat_id:
                LOGGER.warning("GUARDIAN_UNAUTHORIZED_MESSAGE chat_id=%s", chat_id)
                continue
            text = str(message.get("text") or "").strip().lower()
            if text in {"/status", "status"}:
                handler("guardian:status:0", int(message.get("message_id") or 0), update_id, "")
