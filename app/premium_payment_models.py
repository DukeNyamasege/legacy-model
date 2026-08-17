from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utc_now


class PremiumPaymentAttempt(Base):
    """One provider payment attempt; never stores the customer's raw phone number."""

    __tablename__ = "premium_payment_attempts"
    __table_args__ = (
        UniqueConstraint("merchant_reference", name="uq_premium_payment_merchant_reference"),
        UniqueConstraint(
            "provider_transaction_id",
            name="uq_premium_payment_provider_transaction_id",
        ),
        UniqueConstraint(
            "customer_id",
            "idempotency_key",
            name="uq_premium_payment_customer_idempotency",
        ),
        Index("ix_premium_payment_customer_status", "customer_id", "status"),
        Index("ix_premium_payment_expires", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("premium_customers.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="lipana", index=True)
    payment_method: Mapped[str] = mapped_column(String(24), default="mpesa")
    merchant_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(96), nullable=True)

    # KES 250.00 == 25,000 minor units. Integer minor units avoid floating-point
    # ambiguity in the local financial ledger.
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="KES")
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_masked: Mapped[str] = mapped_column(String(32), default="")

    status: Mapped[str] = mapped_column(String(28), default="initiating", index=True)
    provider_status: Mapped[str] = mapped_column(String(80), default="")
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    payment_time_source: Mapped[str] = mapped_column(String(40), default="")

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    provider_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PremiumWebhookEvent(Base):
    """Idempotency ledger for signed payment-provider webhooks.

    Raw webhook bodies are deliberately not persisted because provider payloads can
    contain a phone number. Only the SHA-256 digest and non-sensitive references are
    retained.
    """

    __tablename__ = "premium_webhook_events"
    __table_args__ = (
        UniqueConstraint("event_digest", name="uq_premium_webhook_event_digest"),
        Index("ix_premium_webhook_provider_tx", "provider", "provider_transaction_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="lipana", index=True)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), default="")
    provider_transaction_id: Mapped[str] = mapped_column(String(160), default="")
    payment_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("premium_payment_attempts.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="received", index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
