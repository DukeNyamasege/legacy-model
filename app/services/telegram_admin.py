from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable

import aiohttp
import requests
from sqlalchemy import select

from app.models import AccountRiskState, Trade
from app.token_store import decrypt_auth_payload


ADMIN_CHAT_KEY = "telegram_admin_chat_id"
ADMIN_USERNAME_KEY = "telegram_admin_username"
ADMIN_OFFSET_KEY = "telegram_admin_update_offset"
SESSION_KEY_PREFIX = "telegram_real_session:"
ALERT_KEY_PREFIX = "telegram_admin_last_alert:"

ALERT_STATUSES = {
    "take_profit",
    "stop_loss",
    "insufficient_balance",
    "purchase_insufficient_balance",
    "credential_error",
    "invalid_account",
    "token_required",
    "contract_unavailable",
    "purchase_error",
    "purchase_registration_error",
    "real_disabled",
    "virtual_protection",
    "base_stake_protection",
}


def _normalise_username(value: str) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _admin_username() -> str:
    return _normalise_username(os.getenv("TELEGRAM_ADMIN_USERNAME", "riskmanagerduke"))


def _bot_token(settings: Any) -> str:
    return os.getenv(str(settings.bot_token_env), "").strip()


def _account_payload(repository: Any, config: Any, managed_account_id: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    row = repository.managed_account(int(managed_account_id)) or {}
    secret = str(row.get("token_secret") or "")
    if not secret:
        return None
    try:
        payload = decrypt_auth_payload(secret, config.deriv.token_encryption_key)
    except Exception:
        return None
    return row, payload


def _real_account_context(repository: Any, config: Any, managed_account_id: int) -> dict[str, Any] | None:
    resolved = _account_payload(repository, config, managed_account_id)
    if not resolved:
        return None
    row, payload = resolved
    account_type = str(payload.get("account_type") or "").strip().lower()
    if account_type != "real":
        return None
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        return None
    masked = "***" if len(account_id) <= 6 else f"{account_id[:3]}***{account_id[-3:]}"
    summary = repository.account_summary(account_id, managed_account_id=int(managed_account_id))
    session_profit = 0.0
    protection: dict[str, Any] = {}
    try:
        with repository.database.session() as session:
            state = session.get(AccountRiskState, int(managed_account_id))
            if state is not None:
                session_profit = float(state.session_profit or 0.0)
                protection = {
                    "mode": str(state.protection_mode or "NORMAL_MODE"),
                    "debt": float(state.recovery_loss_debt or 0.0),
                    "consecutive_losses": int(state.consecutive_losses or 0),
                    "virtual_wins": int(state.virtual_win_count or 0),
                    "virtual_losses": int(state.virtual_loss_count or 0),
                }
    except Exception:
        pass
    return {
        "managed_account_id": int(managed_account_id),
        "account_id": account_id,
        "masked": masked,
        "balance": float(summary.get("balance") or 0.0),
        "currency": str(summary.get("currency") or "USD"),
        "trades": int(summary.get("trades") or 0),
        "wins": int(summary.get("wins") or 0),
        "losses": int(summary.get("losses") or 0),
        "profit": float(summary.get("profit") or 0.0),
        "session_profit": session_profit,
        "enabled": bool(row.get("enabled")),
        "execution_status": str(row.get("execution_status") or "inactive"),
        "execution_status_reason": str(row.get("execution_status_reason") or ""),
        "stake_amount": float(row.get("stake_amount") or 0.0),
        "protection": protection,
    }


def _admin_chat_id(repository: Any) -> str:
    env_chat = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    if env_chat:
        return env_chat
    return repository.runtime_preference(ADMIN_CHAT_KEY).strip()


def _send_private_sync(repository: Any, settings: Any, logger: Any, text: str) -> bool:
    token = _bot_token(settings)
    chat_id = _admin_chat_id(repository)
    if not token or not chat_id:
        logger.info("TELEGRAM_ADMIN_ALERT_PENDING reason=admin_chat_not_discovered")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": str(text)[:4096],
                "disable_web_page_preview": "true",
            },
            timeout=float(settings.request_timeout_seconds),
        )
        if response.status_code != 200:
            logger.warning("TELEGRAM_ADMIN_ALERT_FAILED status=%s", response.status_code)
            return False
        return True
    except requests.RequestException as exc:
        logger.warning("TELEGRAM_ADMIN_ALERT_FAILED error=%s", type(exc).__name__)
        return False


def _queue(function: Callable[[], None]) -> None:
    thread = threading.Thread(target=function, daemon=True, name="telegram-admin-alert")
    thread.start()


def _session_key(managed_account_id: int) -> str:
    return f"{SESSION_KEY_PREFIX}{int(managed_account_id)}"


def _read_session(repository: Any, managed_account_id: int) -> dict[str, Any]:
    raw = repository.runtime_preference(_session_key(managed_account_id))
    try:
        payload = json.loads(raw) if raw else {}
        return payload if isinstance(payload, dict) else {}
    except (TypeError, ValueError):
        return {}


def _write_session(repository: Any, managed_account_id: int, payload: dict[str, Any]) -> None:
    repository.set_runtime_preference(
        _session_key(managed_account_id),
        json.dumps(payload, separators=(",", ":")),
    )


def queue_real_lifecycle_alert(
    repository: Any,
    config: Any,
    logger: Any,
    *,
    managed_account_id: int,
    event: str,
    reason: str = "",
) -> None:
    """Queue a private lifecycle message. Demo accounts are deliberately ignored."""

    def work() -> None:
        context = _real_account_context(repository, config, int(managed_account_id))
        if context is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        event_name = str(event or "").strip().lower()
        previous = _read_session(repository, int(managed_account_id))

        if event_name == "start":
            previous = {
                "opening_balance": context["balance"],
                "started_at": now,
                "opening_trades": context["trades"],
                "opening_wins": context["wins"],
                "opening_losses": context["losses"],
            }
            _write_session(repository, int(managed_account_id), previous)
            text = "\n".join(
                (
                    "🟢 REAL AUTO-TRADE STARTED",
                    "",
                    f"Account: {context['masked']}",
                    f"Opening balance: {context['balance']:.2f} {context['currency']}",
                    f"Base stake: {context['stake_amount']:.2f} {context['currency']}",
                    "Status: Joined and waiting for the next qualifying model trade.",
                )
            )
        elif event_name == "resume":
            if not previous:
                previous = {
                    "opening_balance": context["balance"],
                    "started_at": now,
                    "opening_trades": context["trades"],
                    "opening_wins": context["wins"],
                    "opening_losses": context["losses"],
                }
                _write_session(repository, int(managed_account_id), previous)
            text = "\n".join(
                (
                    "▶️ REAL AUTO-TRADE RESUMED",
                    "",
                    f"Account: {context['masked']}",
                    f"Current balance: {context['balance']:.2f} {context['currency']}",
                    f"Session P/L: {context['session_profit']:+.2f} {context['currency']}",
                    "Recovery/session state: preserved.",
                )
            )
        elif event_name == "pause":
            text = "\n".join(
                (
                    "⏸ REAL AUTO-TRADE PAUSED",
                    "",
                    f"Account: {context['masked']}",
                    f"Current balance: {context['balance']:.2f} {context['currency']}",
                    f"Session P/L: {context['session_profit']:+.2f} {context['currency']}",
                    f"Reason: {reason or context['execution_status_reason'] or 'Paused by trader'}",
                    "Recovery/session state: preserved.",
                )
            )
        elif event_name == "stop":
            opening_balance = float(previous.get("opening_balance", context["balance"]) or context["balance"])
            balance_change = context["balance"] - opening_balance
            opening_trades = int(previous.get("opening_trades", context["trades"]) or 0)
            opening_wins = int(previous.get("opening_wins", context["wins"]) or 0)
            opening_losses = int(previous.get("opening_losses", context["losses"]) or 0)
            text = "\n".join(
                (
                    "🔴 REAL AUTO-TRADE STOPPED",
                    "",
                    f"Account: {context['masked']}",
                    f"Opening balance: {opening_balance:.2f} {context['currency']}",
                    f"Closing balance: {context['balance']:.2f} {context['currency']}",
                    f"Balance change: {balance_change:+.2f} {context['currency']}",
                    f"Session P/L: {context['session_profit']:+.2f} {context['currency']}",
                    f"Session trades: {max(0, context['trades'] - opening_trades)}",
                    f"Session wins/losses: {max(0, context['wins'] - opening_wins)}/{max(0, context['losses'] - opening_losses)}",
                    "Next Start Trading begins from the configured base stake.",
                )
            )
            _write_session(repository, int(managed_account_id), {})
        else:
            return
        if _send_private_sync(repository, config.telegram, logger, text):
            logger.info(
                "TELEGRAM_REAL_LIFECYCLE_SENT event=%s account=%s",
                event_name,
                context["masked"],
            )

    _queue(work)


def queue_real_status_alert(
    repository: Any,
    config: Any,
    logger: Any,
    *,
    managed_account_id: int,
    status: str,
    reason: str,
) -> None:
    normalized = str(status or "").strip().lower()
    if normalized not in ALERT_STATUSES:
        if normalized in {"active", "connecting"}:
            repository.set_runtime_preference(f"{ALERT_KEY_PREFIX}{int(managed_account_id)}", "")
        return

    def work() -> None:
        context = _real_account_context(repository, config, int(managed_account_id))
        if context is None:
            return
        signature = json.dumps(
            {"status": normalized, "reason": str(reason or "")[:160]},
            sort_keys=True,
            separators=(",", ":"),
        )
        key = f"{ALERT_KEY_PREFIX}{int(managed_account_id)}"
        if repository.runtime_preference(key) == signature:
            return
        repository.set_runtime_preference(key, signature)
        label = normalized.replace("_", " ").upper()
        text = "\n".join(
            (
                "⚠️ REAL ACCOUNT EXECUTION ALERT",
                "",
                f"Account: {context['masked']}",
                f"Status: {label}",
                f"Balance: {context['balance']:.2f} {context['currency']}",
                f"Session P/L: {context['session_profit']:+.2f} {context['currency']}",
                f"Reason: {reason or 'No additional reason recorded'}",
            )
        )
        _send_private_sync(repository, config.telegram, logger, text)

    _queue(work)


class TelegramAdminController:
    """Private, read-mostly Telegram control plane restricted to one admin user."""

    def __init__(self, repository: Any, config: Any, logger: Any, channel_client: Any) -> None:
        self.repository = repository
        self.config = config
        self.settings = config.telegram
        self.logger = logger
        self.channel_client = channel_client
        self.bot_token = _bot_token(self.settings)
        self.admin_username = _admin_username()

    async def _send_private(self, chat_id: str, text: str) -> bool:
        if not self.bot_token or not chat_id:
            return False
        timeout = aiohttp.ClientTimeout(total=float(self.settings.request_timeout_seconds))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                    data={
                        "chat_id": chat_id,
                        "text": str(text)[:4096],
                        "disable_web_page_preview": "true",
                    },
                ) as response:
                    return response.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False

    def _real_accounts(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for row in self.repository.list_managed_accounts():
            context = _real_account_context(self.repository, self.config, int(row.id))
            if context is not None:
                results.append(context)
        return results

    def _status_text(self) -> str:
        summary = self.repository.summary()
        real_accounts = self._real_accounts()
        enabled = [item for item in real_accounts if item["enabled"]]
        errors = [
            item for item in real_accounts
            if item["execution_status"].lower() not in {"active", "connecting", "validating"}
        ]
        return "\n".join(
            (
                "🤖 LEGACY MODEL VPS STATUS",
                "",
                f"Bot state: {summary.get('status', 'UNKNOWN')}",
                f"Run: {summary.get('run_id', self.config.model.run_id)}",
                f"Real accounts registered: {len(real_accounts)}",
                f"Real accounts joined: {len(enabled)}",
                f"Real accounts needing attention: {len(errors)}",
                f"Open trades: {int(summary.get('open_trades') or 0)}",
                f"Model trades today: {int(summary.get('purchased_trades') or 0)}",
                f"Wins/Losses today: {int(summary.get('wins') or 0)}/{int(summary.get('losses') or 0)}",
            )
        )

    def _errors_text(self) -> str:
        problem_rows = [
            item for item in self._real_accounts()
            if item["execution_status"].lower() not in {"active", "connecting", "validating"}
        ]
        if not problem_rows:
            return "✅ No current REAL-account execution errors are recorded."
        lines = ["⚠️ REAL ACCOUNT ISSUES", ""]
        for item in problem_rows[:15]:
            lines.append(
                f"{item['masked']} — {item['execution_status']}: "
                f"{item['execution_status_reason'] or 'No reason recorded'}"
            )
        if len(problem_rows) > 15:
            lines.append(f"…and {len(problem_rows) - 15} more.")
        return "\n".join(lines)

    def _real_traders_text(self) -> str:
        rows = self._real_accounts()
        enabled = [item for item in rows if item["enabled"]]
        total_balance = sum(item["balance"] for item in enabled)
        return "\n".join(
            (
                "👥 REAL AUTO-TRADERS",
                "",
                f"Registered real accounts: {len(rows)}",
                f"Joined/active real accounts: {len(enabled)}",
                f"Combined joined balance: {total_balance:.2f} USD",
            )
        )

    def _protection_text(self, *, virtual_only: bool = False) -> str:
        rows = self._real_accounts()
        selected = []
        for item in rows:
            mode = str(item["protection"].get("mode") or "")
            if virtual_only:
                if mode == "VIRTUAL_WAITING_FOR_WIN":
                    selected.append(item)
            elif mode in {"VIRTUAL_WAITING_FOR_WIN", "REAL_RECOVERY_PENDING"} or float(item["protection"].get("debt") or 0) > 0:
                selected.append(item)
        title = "🛡 REAL ACCOUNTS IN VIRTUAL MODE" if virtual_only else "♻️ REAL ACCOUNT RECOVERY STATUS"
        if not selected:
            return f"{title}\n\nNone currently."
        lines = [title, ""]
        for item in selected[:15]:
            p = item["protection"]
            lines.append(
                f"{item['masked']} — {p.get('mode')} | debt {float(p.get('debt') or 0):.2f} USD | "
                f"losses {int(p.get('consecutive_losses') or 0)} | virtual wins {int(p.get('virtual_wins') or 0)}/1"
            )
        return "\n".join(lines)

    def _last_trade_text(self) -> str:
        with self.repository.database.session() as session:
            trade = session.scalar(select(Trade).order_by(Trade.purchase_time.desc()).limit(1))
        if trade is None:
            return "No trade has been recorded yet."
        return "\n".join(
            (
                "🧾 LAST RECORDED TRADE",
                "",
                f"Account: {trade.account_id_masked}",
                f"Market: {trade.market}",
                f"Stake: {float(trade.buy_price or 0):.2f} USD",
                f"Outcome: {trade.outcome or 'OPEN'}",
                f"Profit: {float(trade.profit or 0):+.2f} USD",
                f"Purchased: {trade.purchase_time.isoformat()}",
            )
        )

    def _why_text(self, account_query: str) -> str:
        query = str(account_query or "").strip().upper()
        if not query:
            return "Use: /why DOT***315"
        for item in self._real_accounts():
            if item["masked"].upper() == query:
                p = item["protection"]
                return "\n".join(
                    (
                        f"🔎 {item['masked']}",
                        f"Joined: {'YES' if item['enabled'] else 'NO'}",
                        f"Execution status: {item['execution_status']}",
                        f"Reason: {item['execution_status_reason'] or 'No blocking reason recorded'}",
                        f"Balance: {item['balance']:.2f} {item['currency']}",
                        f"Session P/L: {item['session_profit']:+.2f} {item['currency']}",
                        f"Protection mode: {p.get('mode', 'NORMAL_MODE')}",
                        f"Recovery debt: {float(p.get('debt') or 0):.2f} {item['currency']}",
                    )
                )
        return "No REAL account matches that masked account ID."

    def _help_text(self) -> str:
        return "\n".join(
            (
                "MR DUKE PRIVATE VPS COMMANDS",
                "",
                "/status — worker/model and real-account overview",
                "/realtraders — real accounts currently joined",
                "/errors — current real-account execution problems",
                "/recovery — real accounts in recovery/debt",
                "/virtual — real accounts in virtual protection",
                "/lasttrade — most recent recorded trade",
                "/why DOT***315 — explain one real account",
                "/publish-status — send current VPS status to the channel",
                "/publish your message — publish an admin message to the channel",
                "/help — show commands",
                "",
                "Telegram cannot execute shell commands, expose tokens, or reveal VPS secrets.",
            )
        )

    async def _handle_admin_message(self, chat_id: str, text: str) -> None:
        raw = str(text or "").strip()
        lower = raw.lower()
        response = ""
        if lower in {"/start", "start"}:
            response = (
                "✅ Mr Duke private VPS link is active.\n\n"
                "Private lifecycle alerts are restricted to REAL accounts only.\n\n"
                + self._help_text()
            )
        elif lower in {"/status", "status"} or "bot running" in lower or "vps status" in lower:
            response = self._status_text()
        elif lower in {"/realtraders", "real traders", "realtraders"}:
            response = self._real_traders_text()
        elif lower in {"/errors", "errors"} or "what errors" in lower:
            response = self._errors_text()
        elif lower in {"/recovery", "recovery"}:
            response = self._protection_text()
        elif lower in {"/virtual", "virtual"}:
            response = self._protection_text(virtual_only=True)
        elif lower in {"/lasttrade", "last trade", "lasttrade"}:
            response = self._last_trade_text()
        elif lower.startswith("/why ") or lower.startswith("why "):
            response = self._why_text(raw.split(maxsplit=1)[1] if " " in raw else "")
        elif lower == "/publish-status" or "send progress update to the channel" in lower:
            status_text = self._status_text()
            sent = await self.channel_client.send_announcement(status_text)
            response = "✅ Current VPS status was published to the channel." if sent else "❌ Channel publication failed."
        elif lower.startswith("/publish "):
            announcement = raw.split(maxsplit=1)[1].strip()
            sent = bool(announcement) and await self.channel_client.send_announcement(announcement)
            response = "✅ Message published to the channel." if sent else "❌ Channel publication failed."
        elif lower in {"/help", "help"}:
            response = self._help_text()
        else:
            response = (
                "I can answer safe VPS/model questions, but I do not run arbitrary Linux commands.\n\n"
                + self._help_text()
            )
        await self._send_private(chat_id, response)

    async def run(self, is_running: Callable[[], bool]) -> None:
        if not self.bot_token:
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
                            "allowed_updates": json.dumps(["message", "channel_post", "edited_channel_post"]),
                        },
                    ) as response:
                        payload = await response.json(content_type=None)
                if response.status != 200 or not payload.get("ok"):
                    self.logger.warning("TELEGRAM_ADMIN_POLL_FAILED status=%s", response.status)
                    await asyncio.sleep(5)
                    continue

                for update in payload.get("result") or []:
                    update_id = int(update.get("update_id") or 0)
                    offset = max(offset, update_id + 1)
                    self.repository.set_runtime_preference(ADMIN_OFFSET_KEY, str(offset))

                    # Preserve channel discovery if a channel update reaches this
                    # single getUpdates consumer before the hourly publisher does.
                    channel_event = update.get("channel_post") or update.get("edited_channel_post")
                    if isinstance(channel_event, dict) and not self.channel_client.chat_id:
                        chat = channel_event.get("chat") or {}
                        if str(chat.get("type") or "") == "channel" and chat.get("id"):
                            self.channel_client.chat_id = str(chat["id"])
                            self.channel_client.chat_title = str(chat.get("title") or "")
                            self.channel_client._cache_channel()

                    message = update.get("message")
                    if not isinstance(message, dict):
                        continue
                    chat = message.get("chat") or {}
                    sender = message.get("from") or {}
                    if str(chat.get("type") or "") != "private":
                        continue
                    username = _normalise_username(sender.get("username") or chat.get("username") or "")
                    if username != self.admin_username:
                        self.logger.warning("TELEGRAM_ADMIN_UNAUTHORIZED username=%s", username or "missing")
                        continue
                    chat_id = str(chat.get("id") or "").strip()
                    if not chat_id:
                        continue
                    self.repository.set_runtime_preference(ADMIN_CHAT_KEY, chat_id)
                    self.repository.set_runtime_preference(ADMIN_USERNAME_KEY, username)
                    await self._handle_admin_message(chat_id, str(message.get("text") or ""))
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                self.logger.warning("TELEGRAM_ADMIN_POLL_FAILED error=%s", type(exc).__name__)
                await asyncio.sleep(5)
