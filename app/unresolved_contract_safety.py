from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models import Trade, utc_now
from app.repositories.test2_repository import Test2Repository

_INSTALLED = False


def _numeric_contract_id(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text or not text.isdigit():
        return None
    try:
        result = int(text)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 else None


def install_unresolved_contract_safety() -> None:
    """Prevent malformed historical rows from crashing worker startup.

    Real Deriv contract IDs are positive integers. Older tests and compatibility
    paths could persist placeholders such as ``expired-one-tick`` while leaving
    settlement_time NULL. The worker previously called ``int(contract_id)`` for
    every unresolved row and restarted forever when it met one of those values.

    Invalid placeholders are retained for audit/history, marked for manual review,
    and closed with zero financial impact. Valid provider contracts continue into
    the normal private-WebSocket reconciliation path.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original = Test2Repository.unresolved_contracts

    def safe_unresolved_contracts(self: Test2Repository) -> list[Trade]:
        rows = list(original(self))
        valid: list[Trade] = []
        invalid_ids: list[int] = []
        for row in rows:
            if _numeric_contract_id(row.contract_id) is not None:
                valid.append(row)
            else:
                invalid_ids.append(int(row.id))

        if invalid_ids:
            with self.database.session() as session:
                locked = session.scalars(
                    select(Trade)
                    .where(Trade.id.in_(invalid_ids))
                    .with_for_update()
                ).all()
                for row in locked:
                    if row.settlement_time is not None:
                        continue
                    row.settlement_time = utc_now()
                    row.provider_settlement_time = row.provider_settlement_time or row.settlement_time
                    row.outcome = "INVALID_CONTRACT_ID"
                    row.profit = float(row.profit or 0.0)
                    row.requires_manual_review = True
            try:
                self.audit(
                    "MALFORMED_UNRESOLVED_CONTRACTS_QUARANTINED",
                    "worker_startup",
                    "local",
                    {
                        "trade_ids": invalid_ids,
                        "count": len(invalid_ids),
                        "financial_impact": 0,
                    },
                )
            except Exception:
                pass

        return valid

    Test2Repository.unresolved_contracts = safe_unresolved_contracts
    Test2Repository._unresolved_contract_safety_installed = True
    _INSTALLED = True
