from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.models import Trade
from app.repositories.test2_repository import Test2Repository


VERSION = "historical-unresolved-contract-quarantine-v1"
STALE_TICK_CONTRACT_AGE_SECONDS = 30 * 60
_INSTALLED = False
LOGGER = logging.getLogger("deriv_bot")


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _contract_reference_time(trade: Any) -> datetime | None:
    return _aware_utc(
        getattr(trade, "provider_purchase_time", None)
        or getattr(trade, "provider_start_time", None)
        or getattr(trade, "purchase_time", None)
    )


def stale_tick_contract_requires_manual_review(
    trade: Any,
    *,
    now: datetime | None = None,
    age_seconds: int = STALE_TICK_CONTRACT_AGE_SECONDS,
) -> bool:
    """Return True only for expired tick contracts that are no longer live work.

    The platform currently permits Custom Strategy durations up to 100 ticks. A
    database row that is still unsettled after 30 minutes is therefore not an
    active financial contract; it is a reconciliation/accounting record. We do
    not invent its result or profit. It is moved to manual review and removed
    from live execution locks instead.
    """

    if getattr(trade, "settlement_time", None) is not None:
        return False
    if str(getattr(trade, "contract_duration_unit", "") or "").strip().lower() != "t":
        return False
    reference = _contract_reference_time(trade)
    if reference is None:
        return False
    current = _aware_utc(now) or datetime.now(timezone.utc)
    return (current - reference).total_seconds() >= max(60, int(age_seconds))


def _quarantine_expired_tick_rows(repository: Test2Repository) -> list[str]:
    """Persist manual-review state without changing settlement or P/L fields."""

    now = datetime.now(timezone.utc)
    quarantined: list[str] = []
    with repository.database.session() as session:
        rows = list(
            session.scalars(
                select(Trade).where(
                    Trade.settlement_time.is_(None),
                    Trade.requires_manual_review.is_(False),
                    repository._current_run_trade_filter(),
                )
            ).all()
        )
        for trade in rows:
            if not stale_tick_contract_requires_manual_review(trade, now=now):
                continue
            trade.requires_manual_review = True
            quarantined.append(str(trade.contract_id))

    if quarantined:
        LOGGER.warning(
            "HISTORICAL_UNRESOLVED_CONTRACTS_QUARANTINED count=%s "
            "age_threshold_seconds=%s manual_review=true financial_outcome_assumed=false "
            "profit_changed=false settlement_changed=false live_execution_blocked=false",
            len(quarantined),
            STALE_TICK_CONTRACT_AGE_SECONDS,
        )
    return quarantined


def install_historical_unresolved_contract_quarantine() -> None:
    """Keep stale historical rows out of the live execution reconciliation loop."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_unresolved_contracts = Test2Repository.unresolved_contracts

    def unresolved_contracts_live_only(self: Test2Repository) -> list[Trade]:
        _quarantine_expired_tick_rows(self)
        rows = original_unresolved_contracts(self)
        return [
            row
            for row in rows
            if not bool(getattr(row, "requires_manual_review", False))
        ]

    def unresolved_contract_ids_live_only(self: Test2Repository) -> set[int]:
        return {
            int(row.contract_id)
            for row in unresolved_contracts_live_only(self)
            if str(getattr(row, "contract_id", "") or "").isdigit()
        }

    unresolved_contracts_live_only._historical_unresolved_quarantine = True  # type: ignore[attr-defined]
    unresolved_contract_ids_live_only._historical_unresolved_quarantine = True  # type: ignore[attr-defined]
    Test2Repository.unresolved_contracts = unresolved_contracts_live_only
    Test2Repository.unresolved_contract_ids = unresolved_contract_ids_live_only
    Test2Repository._historical_unresolved_contract_quarantine_installed = True
    Test2Repository._historical_unresolved_contract_quarantine_version = VERSION

    LOGGER.warning(
        "HISTORICAL_UNRESOLVED_CONTRACT_QUARANTINE_INSTALLED version=%s "
        "stale_tick_age_seconds=%s manual_review_preserved=true financial_result_unchanged=true",
        VERSION,
        STALE_TICK_CONTRACT_AGE_SECONDS,
    )
    _INSTALLED = True
