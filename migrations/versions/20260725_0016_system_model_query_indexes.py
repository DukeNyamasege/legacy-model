"""add dashboard query indexes for the canonical model ledger

Revision ID: 20260725_0016
Revises: 20260725_0015
"""

from alembic import op


revision = "20260725_0016"
down_revision = "20260725_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_system_model_trades_run_signal",
        "system_model_trades",
        ["run_id", "signal_timestamp"],
    )
    op.create_index(
        "ix_system_model_trades_run_open",
        "system_model_trades",
        ["run_id", "is_virtual", "outcome"],
    )


def downgrade() -> None:
    op.drop_index("ix_system_model_trades_run_open", table_name="system_model_trades")
    op.drop_index("ix_system_model_trades_run_signal", table_name="system_model_trades")
