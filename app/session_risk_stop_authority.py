from __future__ import annotations

import socket
from typing import Any

from app.final_public_controls import PAUSED_STATUSES, STOPPED_STATUSES
from app.models import AccountRiskState, ManagedAccount, utc_now
from app.session_risk_limits import read_session_risk_limits
from enhanced_bot import TradingBot, mask_account_id


LIMIT_STOP_STATUSES = {"take_profit", "stop_loss"}
_INSTALLED = False


def install_limit_stop_status_semantics() -> None:
    """Treat TP/SL as terminal execution states, never resumable pauses."""

    STOPPED_STATUSES.update(LIMIT_STOP_STATUSES)
    PAUSED_STATUSES.difference_update(LIMIT_STOP_STATUSES)


def _managed_id(bot: TradingBot, token: str, state: dict[str, Any]) -> int | None:
    raw = state.get("managed_account_id")
    if raw in {None, ""}:
        raw = bot._managed_account_id_for_token(token)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _session_risk_snapshot(
    bot: TradingBot,
    *,
    managed_account_id: int,
) -> tuple[float, float, float]:
    """Read the exact fresh-Start P/L and frozen signed TP/SL thresholds.

    The worker never falls back to all-time account P/L or stale in-memory limit
    values. Take profit is positive. Stop loss is negative. Both are rounded to
    cents before comparison so the worker, lifecycle API and UI use one value.
    """

    with bot.repository.database.session() as session:
        account = session.get(ManagedAccount, int(managed_account_id))
        risk = session.get(AccountRiskState, int(managed_account_id))
        limits = read_session_risk_limits(
            session,
            int(managed_account_id),
            account=account,
        )
        session_profit = round(float(risk.session_profit or 0.0), 2) if risk else 0.0
    return session_profit, limits.take_profit, limits.stop_loss


def _stop_for_risk_limit(
    bot: TradingBot,
    *,
    token: str,
    account_id: str,
    managed_account_id: int,
    status: str,
    target: float,
    session_profit: float,
    take_profit: float,
    stop_loss: float,
) -> str:
    """Atomically stop execution while preserving the exact hit P/L for display."""

    is_tp = status == "take_profit"
    label = "Take profit" if is_tp else "Stop loss"
    reason = (
        f"{label} target {target:.2f} USD reached at session P/L "
        f"{session_profit:.2f} USD. Auto trading stopped; next Start begins fresh."
    )

    with bot.repository.database.session() as session:
        row = session.get(ManagedAccount, int(managed_account_id), with_for_update=True)
        if row is not None:
            row.enabled = False
            row.execution_status = status
            row.execution_status_reason = reason[:160]
            row.execution_status_updated_at = utc_now()
            row.updated_at = utc_now()

    bot.valid_clients = [
        item
        for item in list(getattr(bot, "valid_clients", []) or [])
        if item[0] != token
    ]
    bot.repository.audit(
        "ACCOUNT_RISK_LIMIT_HARD_STOP",
        "worker",
        socket.gethostname(),
        {
            "account_id_masked": mask_account_id(account_id),
            "managed_account_id": int(managed_account_id),
            "limit": status,
            "limit_target": round(float(target), 2),
            "session_profit": round(float(session_profit), 2),
            "take_profit": round(float(take_profit), 2),
            "stop_loss": round(float(stop_loss), 2),
            "signed_limits": True,
            "execution_stopped": True,
            "next_start_fresh": True,
        },
    )
    bot.logger.warning(
        "ACCOUNT_RISK_LIMIT_HARD_STOP account=%s limit=%s target=%.2f "
        "session_profit=%.2f signed_limits=true stopped=true next_start_fresh=true",
        mask_account_id(account_id),
        status,
        target,
        session_profit,
    )
    return status


def _enforce_session_risk_limit(
    self: TradingBot,
    token: str,
    account_id: str,
    state: dict[str, Any],
) -> str:
    managed_account_id = _managed_id(self, token, state)
    if managed_account_id is None:
        return ""

    session_profit, take_profit, stop_loss = _session_risk_snapshot(
        self,
        managed_account_id=managed_account_id,
    )
    if take_profit > 0 and session_profit >= take_profit:
        return _stop_for_risk_limit(
            self,
            token=token,
            account_id=account_id,
            managed_account_id=managed_account_id,
            status="take_profit",
            target=take_profit,
            session_profit=session_profit,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )
    if stop_loss < 0 and session_profit <= stop_loss:
        return _stop_for_risk_limit(
            self,
            token=token,
            account_id=account_id,
            managed_account_id=managed_account_id,
            status="stop_loss",
            target=stop_loss,
            session_profit=session_profit,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )
    return ""


def install_session_risk_stop_worker() -> None:
    """Make fresh-Start session P/L the final worker authority for TP/SL."""

    global _INSTALLED
    install_limit_stop_status_semantics()
    if _INSTALLED:
        return
    TradingBot._enforce_account_risk_limit = _enforce_session_risk_limit
    TradingBot._session_risk_stop_authority_installed = True
    _INSTALLED = True
