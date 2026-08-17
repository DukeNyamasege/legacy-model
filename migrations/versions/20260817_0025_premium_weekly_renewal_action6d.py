"""premium weekly renewal history for Action 6D

Revision ID: 20260817_0025
Revises: 20260817_0024
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260817_0025"
down_revision = "20260817_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Action 6C/card billing was intentionally skipped. Convert both existing
    # Action 6A identities and future DB-level inserts to manual Lipana M-Pesa
    # renewal. This changes renewal metadata only; paid period timestamps remain
    # untouched and continue to be the entitlement authority.
    op.execute(
        sa.text(
            "UPDATE premium_customers "
            "SET renewal_preference = 'prompt_again', "
            "auto_renew_enabled = false, "
            "renewal_provider = 'lipana'"
        )
    )
    op.alter_column(
        "premium_customers",
        "renewal_preference",
        existing_type=sa.String(length=32),
        server_default="prompt_again",
        existing_nullable=False,
    )
    op.alter_column(
        "premium_customers",
        "auto_renew_enabled",
        existing_type=sa.Boolean(),
        server_default=sa.false(),
        existing_nullable=False,
    )
    op.alter_column(
        "premium_customers",
        "renewal_provider",
        existing_type=sa.String(length=32),
        server_default="lipana",
        existing_nullable=False,
    )

    op.create_table(
        "premium_access_periods",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(length=36),
            sa.ForeignKey("premium_customers.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="lipana"),
        sa.Column("payment_method", sa.String(length=24), nullable=False, server_default="mpesa"),
        sa.Column("payment_reference", sa.String(length=160), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False, server_default="25000"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="KES"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "payment_reference",
            name="uq_premium_access_period_provider_payment",
        ),
    )
    op.create_index(
        "ix_premium_access_periods_customer_id",
        "premium_access_periods",
        ["customer_id"],
    )
    op.create_index(
        "ix_premium_access_period_customer_start",
        "premium_access_periods",
        ["customer_id", "period_start"],
    )
    op.create_index(
        "ix_premium_access_period_customer_end",
        "premium_access_periods",
        ["customer_id", "period_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_premium_access_period_customer_end", table_name="premium_access_periods")
    op.drop_index("ix_premium_access_period_customer_start", table_name="premium_access_periods")
    op.drop_index("ix_premium_access_periods_customer_id", table_name="premium_access_periods")
    op.drop_table("premium_access_periods")

    op.alter_column(
        "premium_customers",
        "renewal_provider",
        existing_type=sa.String(length=32),
        server_default="",
        existing_nullable=False,
    )
    op.alter_column(
        "premium_customers",
        "auto_renew_enabled",
        existing_type=sa.Boolean(),
        server_default=sa.true(),
        existing_nullable=False,
    )
    op.alter_column(
        "premium_customers",
        "renewal_preference",
        existing_type=sa.String(length=32),
        server_default="automatic_if_supported",
        existing_nullable=False,
    )
