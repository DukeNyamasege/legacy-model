"""premium access identity and weekly entitlement for Action 6A

Revision ID: 20260817_0023
Revises: 20260817_0022
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260817_0023"
down_revision = "20260817_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "premium_customers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("identity_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unpaid"),
        sa.Column("plan_code", sa.String(length=40), nullable=False, server_default="weekly_access"),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "renewal_preference",
            sa.String(length=32),
            nullable=False,
            server_default="automatic_if_supported",
        ),
        sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("renewal_provider", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("provider_customer_ref", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("provider_subscription_ref", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("last_payment_provider", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("last_payment_reference", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("renewal_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "identity_fingerprint",
            name="uq_premium_customer_identity_fingerprint",
        ),
    )
    op.create_index(
        "ix_premium_customers_identity_fingerprint",
        "premium_customers",
        ["identity_fingerprint"],
        unique=True,
    )
    op.create_index(
        "ix_premium_customers_status",
        "premium_customers",
        ["status"],
    )
    op.create_index(
        "ix_premium_customers_current_period_end",
        "premium_customers",
        ["current_period_end"],
    )
    op.create_index(
        "ix_premium_customer_status_end",
        "premium_customers",
        ["status", "current_period_end"],
    )

    op.create_table(
        "premium_customer_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(length=36),
            sa.ForeignKey("premium_customers.id"),
            nullable=False,
        ),
        sa.Column(
            "managed_account_id",
            sa.Integer(),
            sa.ForeignKey("managed_accounts.id"),
            nullable=True,
        ),
        sa.Column("account_hash", sa.String(length=64), nullable=False),
        sa.Column("account_masked", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("account_type", sa.String(length=12), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "account_hash",
            name="uq_premium_customer_account_hash",
        ),
        sa.UniqueConstraint(
            "managed_account_id",
            name="uq_premium_customer_managed_account",
        ),
    )
    op.create_index(
        "ix_premium_customer_accounts_customer_id",
        "premium_customer_accounts",
        ["customer_id"],
    )
    op.create_index(
        "ix_premium_customer_accounts_managed_account_id",
        "premium_customer_accounts",
        ["managed_account_id"],
    )
    op.create_index(
        "ix_premium_customer_account_customer",
        "premium_customer_accounts",
        ["customer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_premium_customer_account_customer",
        table_name="premium_customer_accounts",
    )
    op.drop_index(
        "ix_premium_customer_accounts_managed_account_id",
        table_name="premium_customer_accounts",
    )
    op.drop_index(
        "ix_premium_customer_accounts_customer_id",
        table_name="premium_customer_accounts",
    )
    op.drop_table("premium_customer_accounts")

    op.drop_index("ix_premium_customer_status_end", table_name="premium_customers")
    op.drop_index(
        "ix_premium_customers_current_period_end",
        table_name="premium_customers",
    )
    op.drop_index("ix_premium_customers_status", table_name="premium_customers")
    op.drop_index(
        "ix_premium_customers_identity_fingerprint",
        table_name="premium_customers",
    )
    op.drop_table("premium_customers")
