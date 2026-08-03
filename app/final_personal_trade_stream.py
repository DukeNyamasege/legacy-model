from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from fastapi import Request
from sqlalchemy import or_, select

import app.api as base_api
from app.ai_digit_recovery_v1 import VIRTUAL_WINS_REQUIRED
from app.final_public_controls import (
    _current_account_payload,
    _remove_route,
    _reporting_timezone,
    _today_bounds_utc,
    _trade_to_payload,
)
from app.models import (
    AccountRiskState,
    CandidateSignalRecord,
    DirectionalSignal,
    Trade,
    VirtualTrade,
)
from app.repositories.rf_dir5_repository import REAL_RECOVERY_PENDING, VIRTUAL_WAITING_FOR_WIN

_INSTALLED = False


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _sort_time(row: dict[str, Any]) -> datetime:
    return (
        _timestamp(
            row.get("purchase_time")
            or row.get("provider_purchase_time")
            or row.get("created_at")
            or row.get("settlement_time")
        )
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _virtual_outcome(value: Any) -> str:
    raw = str(value or "OPEN").strip().upper()
    if "WIN" in raw:
        return "WIN"
    if "LOSS" in raw:
        return "LOSS"
    if "CANCEL" in raw or "STALE" in raw:
        return "CANCELLED"
    return "OPEN"


def _split_remaining(managed_account_id: int) -> int:
    try:
        value = base_api.REPOSITORY.runtime_preference(
            f"aidr_split_remaining:{int(managed_account_id)}"
        )
        return 1 if int(str(value or "0")) > 0 else 0
    except Exception:
        return 0


def _virtual_rows_with_progress(rows: list[VirtualTrade]) -> list[dict[str, Any]]:
    """Return retained virtual history with each row's original requirement.

    Stop and fresh Start reset the live recovery state but never remove older rows
    from the dashboard. Only the explicit Clear Today / Clear All action deletes
    history. The account's current AIDR status is reported separately by
    `_aidr_summary`; the sequence shown here describes each historical row.
    """

    ordered = sorted(
        rows,
        key=lambda row: _timestamp(row.created_at)
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    streak = 0
    payloads: list[dict[str, Any]] = []
    for row in ordered:
        outcome = _virtual_outcome(row.result)
        barrier = str(row.barrier or "3").strip() or "3"
        stored_progress = re.search(r"progress=(\d+)/(\d+)", str(row.reason or ""))
        row_required = (
            max(1, int(stored_progress.group(2)))
            if stored_progress
            else VIRTUAL_WINS_REQUIRED
            if outcome in {"OPEN", "CANCELLED"}
            else 2
        )
        if outcome == "WIN":
            streak = (
                min(row_required, int(stored_progress.group(1)))
                if stored_progress
                else min(row_required, streak + 1)
            )
            sequence = streak
            progress_text = f"WIN {streak}/{row_required}"
            if streak >= row_required:
                # A completed row arms one real OVER-4 recovery. Any later virtual
                # row belongs to a new cycle after that real recovery lost.
                streak = 0
        elif outcome == "LOSS":
            streak = 0
            sequence = 0
            progress_text = f"LOSS · STREAK 0/{row_required}"
        elif outcome == "CANCELLED":
            sequence = streak
            progress_text = "CANCELLED"
        else:
            sequence = streak
            progress_text = f"PENDING · {streak}/{row_required}"

        payloads.append(
            {
                "id": f"virtual-{int(row.id)}",
                "trade_id": str(row.virtual_trade_id or f"virtual-{int(row.id)}"),
                "virtual_trade_id": str(row.virtual_trade_id or ""),
                "signal_id": str(row.signal_id or ""),
                "is_virtual": True,
                "trade_kind": "virtual",
                "symbol": str(row.market or ""),
                "market": str(row.market or ""),
                "contract_type": f"VIRTUAL OVER {barrier} · {progress_text}",
                "type": "VIRTUAL TRADE",
                "barrier": barrier,
                "buy_price": float(row.simulated_stake or 0.0),
                "stake": float(row.simulated_stake or 0.0),
                "simulated_stake": float(row.simulated_stake or 0.0),
                "payout": float(row.expected_payout) if row.expected_payout is not None else None,
                "expected_payout": (
                    float(row.expected_payout) if row.expected_payout is not None else None
                ),
                "profit": 0.0,
                "actual_profit_loss": 0.0,
                "amount_charged": 0.0,
                "outcome": outcome,
                "virtual_result": str(row.result or "OPEN"),
                "display_result": f"VIRTUAL {progress_text}",
                "virtual_win_sequence": sequence,
                "virtual_wins_required": row_required,
                "history_retained": True,
                "exit_digit": row.actual_last_digit,
                "actual_last_digit": row.actual_last_digit,
                "exit_spot": row.exit_spot,
                "purchase_time": row.created_at.isoformat() if row.created_at else None,
                "provider_purchase_time": row.created_at.isoformat() if row.created_at else None,
                "settlement_time": row.settled_at.isoformat() if row.settled_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "settled_at": row.settled_at.isoformat() if row.settled_at else None,
                "financial_impact_label": "$0.00",
            }
        )
    return payloads


def _aidr_summary(state: AccountRiskState | None, managed_account_id: int) -> dict[str, Any]:
    raw_mode = str(state.protection_mode or "NORMAL_MODE") if state is not None else "NORMAL_MODE"
    wins = int(state.virtual_win_count or 0) if state is not None else 0
    debt = round(float(state.recovery_loss_debt or 0.0), 2) if state is not None else 0.0
    split = _split_remaining(managed_account_id)
    if raw_mode == VIRTUAL_WAITING_FOR_WIN:
        mode = "virtual"
        next_action = f"Virtual OVER-4 confirmation: {wins}/{VIRTUAL_WINS_REQUIRED} wins."
    elif raw_mode == REAL_RECOVERY_PENDING and split > 0:
        mode = "full_recovery"
        next_action = "One real OVER-4 trade will target the full recovery debt."
    elif raw_mode == REAL_RECOVERY_PENDING:
        mode = "exact_recovery"
        next_action = "Next qualifying trade is one real OVER-3 exact recovery."
    else:
        mode = "normal"
        next_action = "Normal OVER-1 execution."
    return {
        "mode": mode,
        "raw_mode": raw_mode,
        "recovery_debt": debt,
        "consecutive_losses": int(state.consecutive_losses or 0) if state is not None else 0,
        "virtual_wins": wins,
        "virtual_wins_required": VIRTUAL_WINS_REQUIRED,
        "virtual_losses": int(state.virtual_loss_count or 0) if state is not None else 0,
        "virtual_observations": int(state.virtual_observation_count or 0) if state is not None else 0,
        "split_recovery_remaining": split,
        "full_recovery_remaining": split,
        "next_action": next_action,
    }


def install_final_personal_trade_stream(app: Any) -> None:
    """Install one final actual + virtual daily history stream.

    Execution lifecycle changes never filter this stream. Stop, Pause and Start
    affect future execution and recovery state only; Clear Today/All owns deletion.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/trades/today", "GET")

    @app.get("/me/trades/today")
    def final_personal_trade_stream(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        start, end = _today_bounds_utc()

        with base_api.DATABASE.session() as session:
            actual_rows = session.execute(
                select(Trade, CandidateSignalRecord, DirectionalSignal)
                .outerjoin(
                    CandidateSignalRecord,
                    CandidateSignalRecord.signal_id == Trade.signal_id,
                )
                .outerjoin(
                    DirectionalSignal,
                    DirectionalSignal.signal_id == Trade.signal_id,
                )
                .where(Trade.managed_account_id == managed_id)
                .where(
                    or_(
                        Trade.purchase_time.between(start, end),
                        Trade.settlement_time.between(start, end),
                        Trade.provider_purchase_time.between(start, end),
                    )
                )
                .order_by(Trade.purchase_time.desc())
                .limit(5000)
            ).all()
            virtual_rows = session.scalars(
                select(VirtualTrade)
                .where(VirtualTrade.managed_account_id == managed_id)
                .where(
                    or_(
                        VirtualTrade.created_at.between(start, end),
                        VirtualTrade.settled_at.between(start, end),
                    )
                )
                .order_by(VirtualTrade.created_at.asc())
                .limit(5000)
            ).all()
            state = session.get(AccountRiskState, managed_id)

        actual_trades = [
            {
                **_trade_to_payload(trade, candidate, directional),
                "is_virtual": False,
                "trade_kind": "actual",
                "history_retained": True,
            }
            for trade, candidate, directional in actual_rows
        ]
        virtual_trades = _virtual_rows_with_progress(list(virtual_rows))
        trades = sorted(
            [*actual_trades, *virtual_trades],
            key=_sort_time,
            reverse=True,
        )

        wins = sum(str(row.get("outcome") or "").upper() == "WIN" for row in actual_trades)
        losses = sum(str(row.get("outcome") or "").upper() == "LOSS" for row in actual_trades)
        open_trades = sum(
            str(row.get("outcome") or "OPEN").upper() not in {"WIN", "LOSS"}
            for row in actual_trades
        )
        profit = sum(float(row.get("profit") or 0.0) for row in actual_trades)
        aidr = _aidr_summary(state, managed_id)

        return {
            "authenticated": True,
            "account": str(account.get("account_id_masked") or ""),
            "account_type": str(account.get("account_type") or "demo"),
            "timezone": str(_reporting_timezone()),
            "date": start.astimezone(_reporting_timezone()).date().isoformat(),
            "session_started_at": None,
            "history_preserved_across_stop": True,
            "trades": trades,
            "aidr": aidr,
            "summary": {
                "total": len(actual_trades),
                "settled": wins + losses,
                "wins": wins,
                "losses": losses,
                "open": open_trades,
                "profit": round(profit, 8),
                "win_rate": wins / (wins + losses) if wins + losses else 0.0,
                "virtual_observations": len(virtual_trades),
                "virtual_wins": int(aidr["virtual_wins"]),
                "virtual_wins_required": VIRTUAL_WINS_REQUIRED,
                "virtual_losses": int(aidr["virtual_losses"]),
                "virtual_open": sum(row.get("outcome") == "OPEN" for row in virtual_trades),
                "history_rows": len(trades),
            },
        }

    app.state.final_personal_trade_stream_installed = True
    _INSTALLED = True
