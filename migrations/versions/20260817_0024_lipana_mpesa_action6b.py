"""Lipana M-Pesa payment attempts and signed webhook idempotency for Action 6B

Revision ID: 20260817_0024
Revises: 20260817_0023
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260817_0024"
down_revision = "20260817_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "premium_payment_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(length=36),
            sa.ForeignKey("premium_customers.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="lipana"),
        sa.Column("payment_method", sa.String(length=24), nullable=False, server_default="mpesa"),
        sa.Column("merchant_reference", sa.String(length=100), nullable=False),
        sa.Column("provider_transaction_id", sa.String(length=160), nullable=True),
        sa.Column("idempotency_key", sa.String(length=96), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="KES"),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("phone_masked", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=28), nullable=False, server_default="initiating"),
        sa.Column("provider_status", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("failure_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("payment_time_source", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "merchant_reference",
            name="uq_premium_payment_merchant_reference",
        ),
        sa.UniqueConstraint(
            "provider_transaction_id",
            name="uq_premium_payment_provider_transaction_id",
        ),
        sa.UniqueConstraint(
            "customer_id",
            "idempotency_key",
            name="uq_premium_payment_customer_idempotency",
        ),
    )
    op.create_index(
        "ix_premium_payment_attempts_customer_id",
        "premium_payment_attempts",
        ["customer_id"],
    )
    op.create_index(
        "ix_premium_payment_attempts_provider",
        "premium_payment_attempts",
        ["provider"],
    )
    op.create_index(
        "ix_premium_payment_attempts_provider_transaction_id",
        "premium_payment_attempts",
        ["provider_transaction_id"],
    )
    op.create_index(
        "ix_premium_payment_attempts_status",
        "premium_payment_attempts",
        ["status"],
    )
    op.create_index(
        "ix_premium_payment_customer_status",
        "premium_payment_attempts",
        ["customer_id", "status"],
    )
    op.create_index(
        "ix_premium_payment_expires",
        "premium_payment_attempts",
        ["status", "expires_at"],
    )

    op.create_table(
        "premium_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="lipana"),
        sa.Column("event_digest", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("provider_transaction_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column(
            "payment_attempt_id",
            sa.String(length=36),
            sa.ForeignKey("premium_payment_attempts.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_digest", name="uq_premium_webhook_event_digest"),
    )
    op.create_index(
        "ix_premium_webhook_events_provider",
        "premium_webhook_events",
        ["provider"],
    )
    op.create_index(
        "ix_premium_webhook_events_payment_attempt_id",
        "premium_webhook_events",
        ["payment_attempt_id"],
    )
    op.create_index(
        "ix_premium_webhook_events_status",
        "premium_webhook_events",
        ["status"],
    )
    op.create_index(
        "ix_premium_webhook_provider_tx",
        "premium_webhook_events",
        ["provider", "provider_transaction_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_premium_webhook_provider_tx", table_name="premium_webhook_events")
    op.drop_index("ix_premium_webhook_events_status", table_name="premium_webhook_events")
    op.drop_index(
        "ix_premium_webhook_events_payment_attempt_id",
        table_name="premium_webhook_events",
    )
    op.drop_index("ix_premium_webhook_events_provider", table_name="premium_webhook_events")
    op.drop_table("premium_webhook_events")

    op.drop_index("ix_premium_payment_expires", table_name="premium_payment_attempts")
    op.drop_index("ix_premium_payment_customer_status", table_name="premium_payment_attempts")
    op.drop_index("ix_premium_payment_attempts_status", table_name="premium_payment_attempts")
    op.drop_index(
        "ix_premium_payment_attempts_provider_transaction_id",
        table_name="premium_payment_attempts",
    )
    op.drop_index("ix_premium_payment_attempts_provider", table_name="premium_payment_attempts")
    op.drop_index("ix_premium_payment_attempts_customer_id", table_name="premium_payment_attempts")
    op.drop_table("premium_payment_attempts")
