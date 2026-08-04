from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import Trade
from app.repositories.test2_repository import Test2Repository, mask_account_id

_INSTALLED = False


def install_trade_registration_idempotency() -> None:
    """Ignore duplicate provider callbacks without losing the original trade.

    A private WebSocket purchase confirmation and a later reconciliation callback
    can describe the same Deriv contract. The former ORM-only implementation tried
    to INSERT twice and emitted a unique-constraint error. One conflict-safe INSERT
    now owns registration; the first committed row remains authoritative.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    def idempotent_register_purchase(
        self: Test2Repository,
        *,
        signal_id: str,
        contract_id: str,
        transaction_id: str,
        account_id: str,
        purchase_time: datetime,
        aligned_with_signal: bool,
        buy_price: float | None = None,
        payout: float | None = None,
        provider_purchase_time: datetime | None = None,
        provider_start_time: datetime | None = None,
        contract_duration: int = 1,
        contract_duration_unit: str = "t",
        managed_account_id: int | None = None,
        bulk_batch_id: str | None = None,
    ) -> None:
        values = {
            "managed_account_id": managed_account_id,
            "bulk_batch_id": bulk_batch_id,
            "trade_id": str(transaction_id or contract_id),
            "signal_id": str(signal_id),
            "contract_id": str(contract_id),
            "account_id_masked": mask_account_id(account_id),
            "purchase_time": purchase_time,
            "provider_purchase_time": provider_purchase_time,
            "provider_start_time": provider_start_time,
            "contract_duration": int(contract_duration),
            "contract_duration_unit": str(contract_duration_unit),
            "buy_price": buy_price,
            "payout": payout,
            "aligned_with_signal": aligned_with_signal,
            "model_version": self.config.model.version,
            "outcome": "OPEN",
            "cumulative_profit": 0.0,
            "drawdown": 0.0,
            "requires_manual_review": False,
        }
        dialect = self.database.engine.dialect.name
        with self.database.session() as session:
            if dialect == "postgresql":
                statement = postgres_insert(Trade).values(**values).on_conflict_do_nothing()
            elif dialect == "sqlite":
                statement = sqlite_insert(Trade).values(**values).on_conflict_do_nothing()
            else:
                # Test and production deployments use SQLite or PostgreSQL. Keep a
                # conservative lookup fallback for any other SQLAlchemy dialect.
                existing = session.query(Trade.id).filter(
                    (Trade.contract_id == str(contract_id))
                    | (
                        (Trade.signal_id == str(signal_id))
                        & (Trade.account_id_masked == mask_account_id(account_id))
                    )
                ).first()
                if existing is not None:
                    return
                statement = Trade.__table__.insert().values(**values)
            result = session.execute(statement)
            inserted = result.rowcount not in {0, None}

        if not inserted:
            try:
                self.audit(
                    "DUPLICATE_PURCHASE_REGISTRATION_IGNORED",
                    "worker",
                    "provider_callback",
                    {
                        "contract_id": str(contract_id),
                        "signal_id": str(signal_id),
                        "managed_account_id": managed_account_id,
                        "financial_impact": 0,
                    },
                )
            except Exception:
                pass

    Test2Repository.register_purchase = idempotent_register_purchase
    Test2Repository._trade_registration_idempotency_installed = True
    _INSTALLED = True
