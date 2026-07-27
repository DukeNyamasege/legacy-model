from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select

from app.models import Trade
from app.repositories.test2_repository import mask_account_id
from app.token_store import decrypt_auth_payload


_INSTALLED = False


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def install_legacy_reference_compatibility(dashboard_consistency_module: Any) -> None:
    """Make the v2 dashboard ledger include historical rows without managed IDs.

    Older production trades can pre-date durable ManagedAccount attribution and
    therefore have ``managed_account_id`` unset even though their masked account
    identity is correct. The Personal Account card already counts those rows by
    account mask. The stable reference ledger must do the same or the global
    dashboard can silently under-count historical executions.

    Period membership deliberately follows ``Trade.purchase_time`` because that
    is the same timestamp used by the Personal Account daily/weekly/monthly
    accounting path. This keeps the trusted account P/L and the global reference
    ledger on the same Africa/Nairobi reporting boundary.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    def reference_trade_rows(
        repository: Any,
        managed_account_id: int,
        *,
        start: datetime,
        end: datetime,
    ) -> list[Trade]:
        start = _aware(start)
        end = _aware(end)

        account_mask = ""
        try:
            managed = repository.managed_account(int(managed_account_id))
            if managed:
                payload = decrypt_auth_payload(
                    managed["token_secret"],
                    repository.config.deriv.token_encryption_key,
                )
                account_id = str(payload.get("account_id") or "").strip()
                if account_id:
                    account_mask = mask_account_id(account_id)
        except Exception:
            # Newer rows still remain addressable by managed_account_id even when
            # a historical credential cannot be decrypted during a read-only
            # dashboard calculation.
            account_mask = ""

        identity_filter = Trade.managed_account_id == int(managed_account_id)
        if account_mask:
            identity_filter = or_(
                identity_filter,
                Trade.account_id_masked == account_mask,
            )

        with repository.database.session() as session:
            rows = session.scalars(
                select(Trade)
                .where(
                    identity_filter,
                    Trade.settlement_time.is_not(None),
                    Trade.outcome.in_(["WIN", "LOSS"]),
                    Trade.profit.is_not(None),
                    Trade.buy_price.is_not(None),
                    Trade.buy_price > 0,
                    repository._current_run_trade_filter(),
                    Trade.purchase_time >= start,
                    Trade.purchase_time < end,
                )
                .order_by(Trade.purchase_time.asc(), Trade.id.asc())
            ).all()
        return list(rows)

    dashboard_consistency_module._reference_trade_rows = reference_trade_rows
    _INSTALLED = True
