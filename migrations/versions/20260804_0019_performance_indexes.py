"""add personal dashboard performance indexes

Revision ID: 20260804_0019
Revises: 20260726_0018
Create Date: 2026-08-04
"""

from __future__ import annotations

from alembic import op

revision = "20260804_0019"
down_revision = "20260726_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Personal dashboard reads filter by one immutable managed-account row and a
    # time range. Separate time indexes allow PostgreSQL to combine the OR branches
    # with bitmap scans instead of walking the full trade history.
    op.create_index(
        "ix_trades_managed_purchase_time_v2",
        "trades",
        ["managed_account_id", "purchase_time"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_trades_managed_settlement_time_v2",
        "trades",
        ["managed_account_id", "settlement_time"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_trades_managed_provider_purchase_v2",
        "trades",
        ["managed_account_id", "provider_purchase_time"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_virtual_managed_created_at_v2",
        "virtual_trades",
        ["managed_account_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_virtual_managed_settled_at_v2",
        "virtual_trades",
        ["managed_account_id", "settled_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_client_sessions_managed_expires_v2",
        "client_sessions",
        ["managed_account_id", "expires_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_client_sessions_managed_expires_v2",
        table_name="client_sessions",
        if_exists=True,
    )
    op.drop_index(
        "ix_virtual_managed_settled_at_v2",
        table_name="virtual_trades",
        if_exists=True,
    )
    op.drop_index(
        "ix_virtual_managed_created_at_v2",
        table_name="virtual_trades",
        if_exists=True,
    )
    op.drop_index(
        "ix_trades_managed_provider_purchase_v2",
        table_name="trades",
        if_exists=True,
    )
    op.drop_index(
        "ix_trades_managed_settlement_time_v2",
        table_name="trades",
        if_exists=True,
    )
    op.drop_index(
        "ix_trades_managed_purchase_time_v2",
        table_name="trades",
        if_exists=True,
    )
