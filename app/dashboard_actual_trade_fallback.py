from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.models import CandidateSignalRecord, ProposalRecord, Trade
from app.repositories.test2_repository import Test2Repository

_INSTALLED = False
_ORIGINAL_SYSTEM_MODEL_TRADES = None
_ORIGINAL_OPEN_SYSTEM_MODEL_TRADE_COUNT = None


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _trade_timestamp(row: Trade) -> datetime | None:
    return _as_utc(getattr(row, "purchase_time", None)) or _as_utc(
        getattr(row, "settlement_time", None)
    )


def _normalized_outcome(value: Any) -> str:
    outcome = str(value or "").strip().upper()
    if outcome in {"WIN", "WON"}:
        return "WIN"
    if outcome in {"LOSS", "LOST"}:
        return "LOSS"
    return outcome


def _profit_ratio(row: Trade, proposal: ProposalRecord | None = None) -> float:
    buy_price = float(getattr(row, "buy_price", 0.0) or 0.0)
    payout = float(getattr(row, "payout", 0.0) or 0.0)
    if buy_price > 0 and payout > buy_price:
        return round((payout - buy_price) / buy_price, 8)
    profit = float(getattr(row, "profit", 0.0) or 0.0)
    if buy_price > 0 and profit > 0:
        return round(profit / buy_price, 8)
    if proposal is not None:
        stake = float(getattr(proposal, "stake", 0.0) or 0.0)
        potential = float(getattr(proposal, "potential_profit", 0.0) or 0.0)
        if stake > 0 and potential > 0:
            return round(potential / stake, 8)
    return 0.90


def _direction(contract_type: str, barrier: str) -> str:
    normalized = str(contract_type or "").strip().upper()
    barrier_text = str(barrier or "").strip()
    if normalized == "PUT":
        return "FALL"
    if normalized == "CALL":
        return "RISE"
    if normalized == "DIGITOVER":
        return f"OVER_{barrier_text or '2'}"
    if normalized == "DIGITUNDER":
        return f"UNDER_{barrier_text or '7'}"
    return normalized or "UNKNOWN"


def _actual_model_trades(
    repository: Test2Repository,
    *,
    start: datetime,
    end: datetime,
    viewer_managed_account_id: int | None = None,
) -> list[dict[str, Any]]:
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    with repository.database.session() as session:
        statement = (
            select(Trade)
            .where(
                Trade.signal_id.is_not(None),
                Trade.signal_id != "",
                Trade.settlement_time.is_not(None),
                Trade.profit.is_not(None),
                Trade.buy_price.is_not(None),
                Trade.buy_price > 0,
            )
            .order_by(Trade.signal_id.asc(), Trade.purchase_time.asc(), Trade.id.asc())
        )
        if viewer_managed_account_id is not None:
            statement = statement.where(Trade.managed_account_id == int(viewer_managed_account_id))
        rows = list(session.scalars(statement).all())
        signal_ids = {str(getattr(row, "signal_id", "") or "").strip() for row in rows}
        signal_ids.discard("")
        signals = {
            str(row.signal_id): row
            for row in session.scalars(
                select(CandidateSignalRecord).where(
                    CandidateSignalRecord.signal_id.in_(signal_ids)
                )
            ).all()
        } if signal_ids else {}
        proposals = {
            str(row.signal_id): row
            for row in session.scalars(
                select(ProposalRecord).where(ProposalRecord.signal_id.in_(signal_ids))
            ).all()
        } if signal_ids else {}

    by_signal: dict[str, Trade] = {}
    for row in rows:
        signal_id = str(getattr(row, "signal_id", "") or "").strip()
        if not signal_id:
            continue
        timestamp = _trade_timestamp(row)
        if timestamp is None or not (start_utc <= timestamp < end_utc):
            continue
        outcome = _normalized_outcome(getattr(row, "outcome", ""))
        if outcome not in {"WIN", "LOSS"}:
            continue
        # One model event per signal. For the current one-account deployment this
        # is exactly the personal trade; after scaling it prevents copier rows
        # from inflating model statistics.
        by_signal.setdefault(signal_id, row)

    events: list[dict[str, Any]] = []
    for signal_id, row in by_signal.items():
        timestamp = _trade_timestamp(row)
        if timestamp is None:
            continue
        signal = signals.get(signal_id)
        proposal = proposals.get(signal_id)
        buy_price = round(float(getattr(row, "buy_price", 0.0) or 0.0), 2)
        actual_profit = round(float(getattr(row, "profit", 0.0) or 0.0), 2)
        actual_payout = round(float(getattr(row, "payout", 0.0) or 0.0), 2)
        ratio = _profit_ratio(row, proposal)
        outcome = _normalized_outcome(getattr(row, "outcome", ""))
        contract_type = str(
            getattr(signal, "contract_type", "")
            or getattr(proposal, "contract_type", "")
            or "DIGITOVER"
        ).strip().upper()
        barrier = str(
            getattr(signal, "barrier", "")
            or getattr(proposal, "barrier", "")
            or ("2" if contract_type == "DIGITOVER" else "")
        ).strip()
        symbol = str(
            getattr(signal, "symbol", "")
            or getattr(proposal, "symbol", "")
            or ""
        ).strip()
        fixed_profit = round(ratio * 0.50, 8) if outcome == "WIN" else -0.50
        duration = getattr(row, "contract_duration", None)
        try:
            duration_ticks = int(duration or 1)
        except (TypeError, ValueError):
            duration_ticks = 1
        settled_at = _as_utc(getattr(row, "settlement_time", None))
        events.append({
            "signal_id": signal_id,
            "run_id": getattr(repository, "run_id", 0),
            "symbol": symbol,
            "direction": _direction(contract_type, barrier),
            "contract_type": contract_type,
            "barrier": barrier,
            "duration_ticks": duration_ticks,
            "signal_timestamp": timestamp.isoformat(),
            "settlement_timestamp": settled_at.isoformat() if settled_at else None,
            "outcome": outcome,
            "is_virtual": False,
            "reference_base_stake": 0.50,
            "fixed_stake_profit": fixed_profit,
            "expected_profit_ratio": ratio,
            "martingale_stake": buy_price,
            "martingale_profit": actual_profit,
            "actual_stake": buy_price,
            "actual_profit": actual_profit,
            "actual_payout": actual_payout,
            "martingale_level": 1 if buy_price > 0.50 + 1e-9 else 0,
            "recovery_debt_before": 0.0,
            "recovery_debt_after": 0.0,
            "source": "actual_trade_fallback",
        })
    events.sort(key=lambda item: (str(item.get("signal_timestamp") or ""), str(item.get("signal_id") or "")))
    return events


def install_dashboard_actual_trade_fallback() -> None:
    global _INSTALLED, _ORIGINAL_SYSTEM_MODEL_TRADES, _ORIGINAL_OPEN_SYSTEM_MODEL_TRADE_COUNT
    if _INSTALLED:
        return

    _ORIGINAL_SYSTEM_MODEL_TRADES = Test2Repository.system_model_trades
    _ORIGINAL_OPEN_SYSTEM_MODEL_TRADE_COUNT = Test2Repository.open_system_model_trade_count

    def system_model_trades_with_actual_fallback(
        self: Test2Repository,
        *,
        start: datetime,
        end: datetime,
        include_virtual: bool = False,
        viewer_managed_account_id: int | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        canonical = list(
            _ORIGINAL_SYSTEM_MODEL_TRADES(
                self,
                start=start,
                end=end,
                include_virtual=include_virtual,
                viewer_managed_account_id=viewer_managed_account_id,
                **kwargs,
            )
        )
        merged: dict[str, dict[str, Any]] = {}
        for item in _actual_model_trades(
            self,
            start=start,
            end=end,
            viewer_managed_account_id=viewer_managed_account_id,
        ):
            signal_id = str(item.get("signal_id") or "")
            if signal_id:
                merged[signal_id] = item
        for item in canonical:
            signal_id = str(item.get("signal_id") or "")
            if signal_id:
                merged.setdefault(signal_id, item)
        values = list(merged.values())
        values.sort(key=lambda item: (str(item.get("signal_timestamp") or ""), str(item.get("signal_id") or "")))
        return values

    def open_system_model_trade_count_with_actual_fallback(self: Test2Repository) -> int:
        canonical_count = int(_ORIGINAL_OPEN_SYSTEM_MODEL_TRADE_COUNT(self) or 0)
        with self.database.session() as session:
            actual_count = int(
                session.scalar(
                    select(func.count(func.distinct(Trade.signal_id))).where(
                        Trade.signal_id.is_not(None),
                        Trade.signal_id != "",
                        Trade.settlement_time.is_(None),
                    )
                )
                or 0
            )
        return max(canonical_count, actual_count)

    Test2Repository.system_model_trades = system_model_trades_with_actual_fallback
    Test2Repository.open_system_model_trade_count = open_system_model_trade_count_with_actual_fallback
    Test2Repository._dashboard_actual_trade_fallback_installed = True
    _INSTALLED = True
