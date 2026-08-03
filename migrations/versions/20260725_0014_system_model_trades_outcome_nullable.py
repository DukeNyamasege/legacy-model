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


def _column_nullable(table_name: str, column_name: str) -> bool | None:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return None
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return bool(column["nullable"])
    return None


def upgrade() -> None:
    if _column_nullable("system_model_trades", "outcome") is False:
        op.alter_column(
            "system_model_trades",
            "outcome",
            existing_type=sa.VARCHAR(length=10),
            nullable=True,
        )


def downgrade() -> None:
    if _column_nullable("system_model_trades", "outcome") is True:
        op.alter_column(
            "system_model_trades",
            "outcome",
            existing_type=sa.VARCHAR(length=10),
            nullable=False,
        )
