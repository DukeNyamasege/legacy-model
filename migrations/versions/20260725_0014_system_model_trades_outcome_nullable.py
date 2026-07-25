"""Make system_model_trades.outcome nullable for pending trades.

Revision ID: 20260725_0014
Revises: 20260725_0013
Create Date: 2026-07-25 00:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260725_0014"
down_revision = "20260725_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "system_model_trades",
        "outcome",
        existing_type=sa.VARCHAR(length=10),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "system_model_trades",
        "outcome",
        existing_type=sa.VARCHAR(length=10),
        nullable=False,
    )
