from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utc_now


class PremiumCustomer(Base):
    """One paid-access identity shared by a trader's linked DOT/ROT accounts."""

    __tablename__ = "premium_customers"
    __table_args__ = (
        Index("ix_premium_customer_status_end", "status", "current_period_end"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity_fingerprint: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="unpaid", index=True)
    plan_code: Mapped[str] = mapped_column(String(40), default="weekly_access")

    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    # Payment-provider wiring is added in Actions 6B/6C. These fields let those
    # providers attach recurring/subscription references without changing the
    # premium identity or access semantics introduced in 6A.
    renewal_preference: Mapped[str] = mapped_column(
        String(32), default="automatic_if_supported"
    )
    auto_renew_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    renewal_provider: Mapped[str] = mapped_column(String(32), default="")
    provider_customer_ref: Mapped[str] = mapped_column(String(160), default="")
    provider_subscription_ref: Mapped[str] = mapped_column(String(160), default="")
    last_payment_provider: Mapped[str] = mapped_column(String(32), default="")
    last_payment_reference: Mapped[str] = mapped_column(String(160), default="")
    renewal_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PremiumCustomerAccount(Base):
    """Stable account-to-premium mapping without storing a raw Deriv account ID."""

    __tablename__ = "premium_customer_accounts"
    __table_args__ = (
        UniqueConstraint("account_hash", name="uq_premium_customer_account_hash"),
        UniqueConstraint(
            "managed_account_id",
            name="uq_premium_customer_managed_account",
        ),
        Index("ix_premium_customer_account_customer", "customer_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("premium_customers.id"), nullable=False, index=True
    )
    managed_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("managed_accounts.id"), nullable=True, index=True
    )
    account_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    account_masked: Mapped[str] = mapped_column(String(50), default="")
    account_type: Mapped[str] = mapped_column(String(12), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
