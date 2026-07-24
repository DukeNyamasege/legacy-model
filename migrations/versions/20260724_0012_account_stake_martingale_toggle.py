"""Add per-account martingale toggle and allow custom stake amounts.

Revision ID: 20260724_0012
Revises: 20260722_0011
Create Date: 2026-07-24 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260724_0012"
down_revision = "20260722_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managed_accounts",
        sa.Column(
            "martingale_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("managed_accounts", "martingale_enabled")
