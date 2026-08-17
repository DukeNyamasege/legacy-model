from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utc_now


class PremiumAccessPeriod(Base):
    """Immutable seven-day entitlement created by one verified M-Pesa payment."""

    __tablename__ = "premium_access_periods"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "payment_reference",
            name="uq_premium_access_period_provider_payment",
        ),
        Index("ix_premium_access_period_customer_start", "customer_id", "period_start"),
        Index("ix_premium_access_period_customer_end", "customer_id", "period_end"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("premium_customers.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="lipana")
    payment_method: Mapped[str] = mapped_column(String(24), nullable=False, default="mpesa")
    payment_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=25000)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KES")
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
