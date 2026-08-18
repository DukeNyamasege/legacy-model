from __future__ import annotations

"""Keep per-Trade cumulative P/L and drawdown scoped to the account session."""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.models import RuntimePreference, Trade
from app.repositories.test2_repository import Test2Repository


_INSTALLED = False
_ORIGINAL_SETTLE_TRADE: Any = None
_SESSION_PREFIX = "session_risk_limits:v1:"


def _session_start(session: Any, managed_id: int) -> datetime | None:
    row = session.get(RuntimePreference, f"{_SESSION_PREFIX}{int(managed_id)}")
    if row is None:
        return None
    try:
        payload = json.loads(str(row.preference_value or "{}"))
        return datetime.fromisoformat(str(payload.get("started_at") or "").replace("Z", "+00:00"))
    except Exception:
        return None


def repair_account_session_trade_metrics(repository: Test2Repository, managed_id: int) -> int:
    repaired = 0
    with repository.database.session() as session:
        started_at = _session_start(session, int(managed_id))
        if started_at is None:
            return 0
        rows = list(
            session.scalars(
                select(Trade)
                .where(
                    Trade.managed_account_id == int(managed_id),
                    Trade.settlement_time.is_not(None),
                    Trade.purchase_time >= started_at,
                )
                .order_by(Trade.settlement_time.asc(), Trade.id.asc())
            ).all()
        )
        cumulative = 0.0
        high_water = 0.0
        for row in rows:
            cumulative = round(cumulative + float(row.profit or 0.0), 8)
            high_water = max(high_water, cumulative)
            row.cumulative_profit = round(cumulative, 8)
            row.drawdown = round(max(0.0, high_water - cumulative), 8)
            repaired += 1
    return repaired


def install_account_trade_metrics_authority() -> None:
    global _INSTALLED, _ORIGINAL_SETTLE_TRADE
    if _INSTALLED:
        return

    _ORIGINAL_SETTLE_TRADE = Test2Repository.settle_trade

    def settle_trade_with_account_metrics(self: Test2Repository, **kwargs: Any) -> bool:
        original = _ORIGINAL_SETTLE_TRADE
        if original is None:
            return False
        settled = bool(original(self, **kwargs))
        if not settled:
            return False
        contract_id = str(kwargs.get("contract_id") or "")
        try:
            with self.database.session() as session:
                managed_id = session.scalar(
                    select(Trade.managed_account_id).where(Trade.contract_id == contract_id)
                )
            if managed_id is not None:
                repair_account_session_trade_metrics(self, int(managed_id))
        except Exception:
            # Trade settlement is already durable. Reporting repair can retry at
            # worker startup and must never turn a settled provider contract into
            # an execution failure.
            pass
        return True

    Test2Repository.settle_trade = settle_trade_with_account_metrics
    Test2Repository._account_trade_metrics_authority_installed = True
    _INSTALLED = True
