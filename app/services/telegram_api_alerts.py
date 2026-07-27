from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.telegram_admin import (
    _queue,
    _read_session,
    _real_account_context,
    _send_private_sync,
    _write_session,
)


def queue_real_api_lifecycle_alert(
    repository: Any,
    config: Any,
    logger: Any,
    *,
    managed_account_id: int,
    event: str,
    reason: str = "",
) -> None:
    """Capture lifecycle facts synchronously, then send without delaying the API.

    Capturing before Stop is important because Stop intentionally clears the
    account's recovery/session state. Demo accounts return immediately and never
    generate private admin notifications.
    """
    context = _real_account_context(repository, config, int(managed_account_id))
    if context is None:
        return

    event_name = str(event or "").strip().lower()
    now = datetime.now(timezone.utc).isoformat()
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
                f"Balance change: {context['balance'] - opening_balance:+.2f} {context['currency']}",
                f"Session P/L: {context['session_profit']:+.2f} {context['currency']}",
                f"Session trades: {max(0, context['trades'] - opening_trades)}",
                f"Session wins/losses: {max(0, context['wins'] - opening_wins)}/{max(0, context['losses'] - opening_losses)}",
                "Next Start Trading begins from the configured base stake.",
            )
        )
        _write_session(repository, int(managed_account_id), {})
    else:
        return

    def send() -> None:
        if _send_private_sync(repository, config.telegram, logger, text):
            logger.info(
                "TELEGRAM_REAL_LIFECYCLE_SENT event=%s account=%s",
                event_name,
                context["masked"],
            )

    _queue(send)
