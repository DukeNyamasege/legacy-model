"""preserve exact provider contract spots and entry digit

Revision ID: 20260819_0026
Revises: 20260817_0025
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260819_0026"
down_revision = "20260817_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("entry_spot_display", sa.String(length=100), nullable=True))
    op.add_column("trades", sa.Column("exit_spot_display", sa.String(length=100), nullable=True))
    op.add_column("trades", sa.Column("entry_digit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "entry_digit")
    op.drop_column("trades", "exit_spot_display")
    op.drop_column("trades", "entry_spot_display")
