from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from fastapi import Request
from sqlalchemy import or_, select

import app.api as base_api
from app.ai_digit_recovery_v1 import VIRTUAL_WINS_REQUIRED
from app.custom_strategy_comparator_extension import (
    install_custom_strategy_comparator_extension,
)
from app.custom_strategy_v1 import contract_for_config, read_custom_strategy
from app.custom_strategy_virtual_hook import virtual_hook_settings_from_session
from app.custom_virtual_contract_parity import virtual_contract_display
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


install_custom_strategy_comparator_extension()
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
    # Virtual Hook never purchases a Deriv contract, so an infrastructure miss is
    # not a contract cancellation. Historical STALE/CANCEL rows and the new
    # VIRTUAL_VOID_RETRY state are displayed as a zero-impact VOID that does not
    # count as either a virtual win or loss.
    if "VOID" in raw or "RETRY" in raw or "CANCEL" in raw or "STALE" in raw:
        return "VOID"
    return "OPEN"


def _split_remaining(managed_account_id: int) -> int:
    try:
        value = base_api.REPOSITORY.runtime_preference(
            f"aidr_split_remaining:{int(managed_account_id)}"
        )
        return 1 if int(str(value or "0")) > 0 else 0
    except Exception:
        return 0


def _current_virtual_requirement(managed_account_id: int) -> int:
    try:
        with base_api.DATABASE.session() as session:
            hook = virtual_hook_settings_from_session(session, int(managed_account_id))
        return max(1, int(hook.exit_after_consecutive_wins)) if hook.enabled else 1
    except Exception:
        return max(1, int(VIRTUAL_WINS_REQUIRED or 1))


def _current_custom_contract_label(managed_account_id: int) -> str:
    try:
        config = read_custom_strategy(base_api.DATABASE, int(managed_account_id))
        contract_type, direction, barrier = contract_for_config(config)
        return virtual_contract_display(
            contract_type,
            barrier=barrier,
            direction=direction,
        )
    except Exception:
        return "CUSTOM STRATEGY"


def _virtual_rows_with_progress(
    rows: list[VirtualTrade],
    *,
    managed_account_id: int = 0,
) -> list[dict[str, Any]]:
    """Return virtual history using the exact contract stored on each observation.

    Settled legacy rows created before progress snapshots keep their historical
    two-win grouping. Current rows persist `progress=x/y`, so the exact hook
    requirement travels with each observation instead of being reinterpreted.
    VOID rows have no financial or progress effect and simply require a fresh
    qualifying zero-stake observation.
    """

    ordered = sorted(
        rows,
        key=lambda row: _timestamp(getattr(row, "created_at", None))
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    streak = 0
    payloads: list[dict[str, Any]] = []
    current_required = _current_virtual_requirement(managed_account_id) if managed_account_id else 1
    # Rows produced before progress=x/y was persisted belonged to the historical
    # two-win AIDR presentation. This value is historical data semantics, not the
    # current runtime setting. New settled rows persist their own exact requirement.
    legacy_required = 2
    for row in ordered:
        outcome = _virtual_outcome(getattr(row, "result", "OPEN"))
        stored_progress = re.search(
            r"progress=(\d+)/(\d+)",
            str(getattr(row, "reason", "") or ""),
        )
        if stored_progress:
            row_required = max(1, int(stored_progress.group(2)))
        elif outcome in {"WIN", "LOSS"}:
            row_required = legacy_required
        else:
            row_required = current_required

        if outcome == "WIN":
            streak = (
                min(row_required, int(stored_progress.group(1)))
                if stored_progress
                else min(row_required, streak + 1)
            )
            sequence = streak
            progress_text = f"WIN {streak}/{row_required}"
            if streak >= row_required:
                streak = 0
        elif outcome == "LOSS":
            streak = 0
            sequence = 0
            progress_text = f"LOSS · STREAK 0/{row_required}"
        elif outcome == "VOID":
            sequence = streak
            progress_text = "VOID · RETRY"
        else:
            sequence = streak
            progress_text = f"PENDING · {streak}/{row_required}"

        contract_type = str(getattr(row, "contract_type", "") or "")
        barrier = str(getattr(row, "barrier", "") or "")
        direction = str(getattr(row, "direction", "") or "")
        contract_label = virtual_contract_display(
            contract_type,
            barrier=barrier,
            direction=direction,
        )
        created_at = getattr(row, "created_at", None)
        settled_at = getattr(row, "settled_at", None)
        expected_payout = getattr(row, "expected_payout", None)
        actual_last_digit = getattr(row, "actual_last_digit", None)
        exit_spot = getattr(row, "exit_spot", None)
        prediction_digit = getattr(row, "prediction_digit", None)
        row_id = int(getattr(row, "id", 0))
        virtual_trade_id = str(getattr(row, "virtual_trade_id", "") or "")
        signal_id = str(getattr(row, "signal_id", "") or "")
        market = str(getattr(row, "market", "") or "")
        simulated_stake = float(getattr(row, "simulated_stake", 0.0) or 0.0)
        raw_result = str(getattr(row, "result", "OPEN") or "OPEN")

        payloads.append(
            {
                "id": f"virtual-{row_id}",
                "trade_id": virtual_trade_id or f"virtual-{row_id}",
                "virtual_trade_id": virtual_trade_id,
                "signal_id": signal_id,
                "is_virtual": True,
                "trade_kind": "virtual",
                "symbol": market,
                "market": market,
                "contract_type": f"VIRTUAL HOOK · {contract_label} · {progress_text}",
                "type": "VIRTUAL HOOK",
                "barrier": barrier,
                "prediction": prediction_digit,
                "buy_price": simulated_stake,
                "stake": simulated_stake,
                "simulated_stake": simulated_stake,
                "payout": float(expected_payout) if expected_payout is not None else None,
                "expected_payout": (
                    float(expected_payout) if expected_payout is not None else None
                ),
                "profit": 0.0,
                "actual_profit_loss": 0.0,
                "amount_charged": 0.0,
                "outcome": outcome,
                "virtual_result": raw_result,
                "display_result": f"VIRTUAL {progress_text}",
                "virtual_win_sequence": sequence,
                "virtual_wins_required": row_required,
                "history_retained": True,
                "exit_digit": actual_last_digit,
                "actual_last_digit": actual_last_digit,
                "exit_spot": exit_spot,
                "purchase_time": created_at.isoformat() if created_at else None,
                "provider_purchase_time": created_at.isoformat() if created_at else None,
                "settlement_time": settled_at.isoformat() if settled_at else None,
                "created_at": created_at.isoformat() if created_at else None,
                "settled_at": settled_at.isoformat() if settled_at else None,
                "financial_impact_label": "$0.00",
            }
        )
    return payloads


def _aidr_summary(state: AccountRiskState | None, managed_account_id: int) -> dict[str, Any]:
    raw_mode = str(state.protection_mode or "NORMAL_MODE") if state is not None else "NORMAL_MODE"
    wins = int(state.virtual_win_count or 0) if state is not None else 0
    debt = round(float(state.recovery_loss_debt or 0.0), 2) if state is not None else 0.0
    split = _split_remaining(managed_account_id)
    # This endpoint is the final Custom Strategy personal stream. The user's saved
    # Virtual Hook setting is authoritative; legacy AIDR trap escalation must not
    # silently change one configured virtual win into two or three.
    required = _current_virtual_requirement(managed_account_id)
    contract_label = _current_custom_contract_label(managed_account_id)
    if raw_mode == VIRTUAL_WAITING_FOR_WIN:
        mode = "virtual"
        next_action = f"Virtual {contract_label} mirror: {wins}/{required} wins."
    elif raw_mode == REAL_RECOVERY_PENDING and split > 0:
        mode = "full_recovery"
        next_action = f"Next future qualifying real {contract_label} trade continues recovery."
    elif raw_mode == REAL_RECOVERY_PENDING:
        mode = "exact_recovery"
        next_action = f"Next future qualifying real {contract_label} trade continues recovery."
    else:
        mode = "normal"
        next_action = f"Normal {contract_label} Custom Strategy execution."
    return {
        "mode": mode,
        "raw_mode": raw_mode,
        "recovery_debt": debt,
        "consecutive_losses": int(state.consecutive_losses or 0) if state is not None else 0,
        "virtual_wins": wins,
        "virtual_wins_required": required,
        "virtual_losses": int(state.virtual_loss_count or 0) if state is not None else 0,
        "virtual_observations": int(state.virtual_observation_count or 0) if state is not None else 0,
        "split_recovery_remaining": split,
        "full_recovery_remaining": split,
        "next_action": next_action,
        "custom_contract": contract_label,
    }


def install_final_personal_trade_stream(app: Any) -> None:
    """Install one final actual + exact-contract virtual daily history stream."""

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
        virtual_trades = _virtual_rows_with_progress(
            list(virtual_rows),
            managed_account_id=managed_id,
        )
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
                "virtual_wins_required": int(aidr["virtual_wins_required"]),
                "virtual_losses": int(aidr["virtual_losses"]),
                "virtual_open": sum(row.get("outcome") == "OPEN" for row in virtual_trades),
                "virtual_void": sum(row.get("outcome") == "VOID" for row in virtual_trades),
                "history_rows": len(trades),
            },
        }

    app.state.final_personal_trade_stream_installed = True
    _INSTALLED = True
