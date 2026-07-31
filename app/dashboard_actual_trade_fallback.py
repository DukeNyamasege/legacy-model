from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.models import Trade
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


def _profit_ratio(row: Trade) -> float:
    buy_price = float(getattr(row, "buy_price", 0.0) or 0.0)
    payout = float(getattr(row, "payout", 0.0) or 0.0)
    if buy_price > 0 and payout > buy_price:
        return round((payout - buy_price) / buy_price, 8)
    profit = float(getattr(row, "profit", 0.0) or 0.0)
    if buy_price > 0 and profit > 0:
        return round(profit / buy_price, 8)
    return 0.90


def _actual_model_trades(
    repository: Test2Repository,
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Build one dashboard model event per settled purchased signal.

    The canonical SystemModelTrade ledger is preferred. Missing/stale signal_ids
    are filled from the earliest settled monetary Trade row, so the global board
    can show the same actual P/L as the personal account when only one trader is
    active, while still avoiding copy-account double counting.
    """

    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    with repository.database.session() as session:
        rows = list(
            session.scalars(
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
            ).all()
        )

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
        by_signal.setdefault(signal_id, row)

    events: list[dict[str, Any]] = []
    for signal_id, row in by_signal.items():
        timestamp = _trade_timestamp(row)
        if timestamp is None:
            continue
        buy_price = max(1e-9, float(getattr(row, "buy_price", 0.0) or 0.0))
        actual_profit = float(getattr(row, "profit", 0.0) or 0.0)
        actual_payout = float(getattr(row, "payout", 0.0) or 0.0)
        ratio = _profit_ratio(row)
        fixed_profit = round(ratio * 0.50, 8) if actual_profit > 0 else -0.50
        duration = getattr(row, "contract_duration", None)
        try:
            duration_ticks = int(duration or 1)
        except (TypeError, ValueError):
            duration_ticks = 1
        events.append(
            {
                "signal_id": signal_id,
                "run_id": getattr(row, "run_id", getattr(repository, "run_id", 0)),
                "symbol": str(getattr(row, "symbol", "") or ""),
                "direction": "OVER_2",
                "contract_type": str(getattr(row, "contract_type", "") or "DIGITOVER"),
                "duration_ticks": duration_ticks,
                "signal_timestamp": timestamp.isoformat(),
                "settlement_timestamp": _as_utc(
                    getattr(row, "settlement_time", None)
                ).isoformat()
                if _as_utc(getattr(row, "settlement_time", None))
                else None,
                "outcome": _normalized_outcome(getattr(row, "outcome", "")),
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
            }
        )
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
    ) -> list[dict[str, Any]]:
        canonical = list(
            _ORIGINAL_SYSTEM_MODEL_TRADES(
                self,
                start=start,
                end=end,
                include_virtual=include_virtual,
            )
        )
        merged: dict[str, dict[str, Any]] = {}
        for item in _actual_model_trades(self, start=start, end=end):
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

    def open_system_model_trade_count_with_actual_fallback(
        self: Test2Repository,
    ) -> int:
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
