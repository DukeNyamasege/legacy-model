from __future__ import annotations

"""Keep per-Trade cumulative P/L and drawdown scoped to the account session.

Historical rows are repaired once when the worker starts (or once on the first
settlement of a newly-created session). Normal settlements then update one durable
per-account metrics cursor instead of rescanning the whole trade session.
"""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.models import RuntimePreference, Trade, utc_now
from app.repositories.test2_repository import Test2Repository


_INSTALLED = False
_ORIGINAL_SETTLE_TRADE: Any = None
_SESSION_PREFIX = "session_risk_limits:v1:"
_METRICS_PREFIX = "account_trade_metrics:v1:"


def _session_start(session: Any, managed_id: int) -> datetime | None:
    row = session.get(RuntimePreference, f"{_SESSION_PREFIX}{int(managed_id)}")
    if row is None:
        return None
    try:
        payload = json.loads(str(row.preference_value or "{}"))
        return datetime.fromisoformat(str(payload.get("started_at") or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _metrics_key(managed_id: int) -> str:
    return f"{_METRICS_PREFIX}{int(managed_id)}"


def _metrics_payload(row: RuntimePreference | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(str(row.preference_value or "{}"))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _write_metrics_cursor(
    session: Any,
    *,
    managed_id: int,
    started_at: datetime,
    cumulative: float,
    high_water: float,
) -> None:
    key = _metrics_key(managed_id)
    payload = json.dumps(
        {
            "started_at": started_at.isoformat(),
            "cumulative_profit": round(float(cumulative), 8),
            "high_water": round(float(high_water), 8),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    row = session.get(RuntimePreference, key, with_for_update=True)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=payload))
    else:
        row.preference_value = payload
        row.updated_at = utc_now()


def repair_account_session_trade_metrics(repository: Test2Repository, managed_id: int) -> int:
    """Repair one session in settlement order and initialize its incremental cursor."""

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
        _write_metrics_cursor(
            session,
            managed_id=int(managed_id),
            started_at=started_at,
            cumulative=cumulative,
            high_water=high_water,
        )
    return repaired


def _apply_incremental_trade_metrics(
    repository: Test2Repository,
    *,
    contract_id: str,
) -> bool:
    """Correct one newly-settled trade without an O(session-trades) rescan."""

    with repository.database.session() as session:
        trade = session.scalar(
            select(Trade)
            .where(Trade.contract_id == str(contract_id))
            .with_for_update()
        )
        if (
            trade is None
            or trade.settlement_time is None
            or trade.managed_account_id is None
        ):
            return False

        managed_id = int(trade.managed_account_id)
        started_at = _session_start(session, managed_id)
        if started_at is None or trade.purchase_time < started_at:
            return False

        cursor = session.get(RuntimePreference, _metrics_key(managed_id), with_for_update=True)
        payload = _metrics_payload(cursor)
        if str(payload.get("started_at") or "") != started_at.isoformat():
            return False

        try:
            cumulative = round(
                float(payload.get("cumulative_profit") or 0.0)
                + float(trade.profit or 0.0),
                8,
            )
            high_water = max(float(payload.get("high_water") or 0.0), cumulative)
        except (TypeError, ValueError):
            return False

        trade.cumulative_profit = cumulative
        trade.drawdown = round(max(0.0, high_water - cumulative), 8)
        _write_metrics_cursor(
            session,
            managed_id=managed_id,
            started_at=started_at,
            cumulative=cumulative,
            high_water=high_water,
        )
        return True


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
            if _apply_incremental_trade_metrics(self, contract_id=contract_id):
                return True
            # First settlement of a new session, or a legacy cursor mismatch: do
            # one bounded account-session repair and seed the cursor. Subsequent
            # settlements return to the constant-size incremental path.
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
    Test2Repository._account_trade_metrics_policy = "startup_repair_then_incremental_cursor"
    _INSTALLED = True
