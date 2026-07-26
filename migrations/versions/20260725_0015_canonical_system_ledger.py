"""complete canonical system-model ledger

Revision ID: 20260725_0015
Revises: 20260725_0014
"""

from alembic import op
import sqlalchemy as sa


revision = "20260725_0015"
down_revision = "20260725_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_model_trades", sa.Column("entry_tick_sequence", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("system_model_trades", sa.Column("expiry_tick_sequence", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("system_model_trades", sa.Column("entry_spot", sa.Float(), nullable=False, server_default="0"))
    op.add_column("system_model_trades", sa.Column("exit_spot", sa.Float(), nullable=True))
    op.add_column("system_model_trades", sa.Column("expected_profit_ratio", sa.Float(), nullable=False, server_default="0.90"))
    op.create_index("ix_system_model_trades_entry_tick_sequence", "system_model_trades", ["entry_tick_sequence"])
    op.create_index("ix_system_model_trades_expiry_tick_sequence", "system_model_trades", ["expiry_tick_sequence"])
    op.create_index("ix_system_model_trades_settlement_timestamp", "system_model_trades", ["settlement_timestamp"])
    op.create_table(
        "system_model_states",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("test_runs.id"), primary_key=True),
        sa.Column("mode", sa.String(length=30), nullable=False, server_default="REAL"),
        sa.Column("consecutive_real_losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_virtual_wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Rows opened by the former incomplete ledger have no entry quote/tick and
    # therefore cannot be settled truthfully. Keep them for audit, but never
    # fabricate an outcome from the first post-deploy tick.
    op.execute("UPDATE system_model_trades SET outcome = 'STALE' WHERE outcome IS NULL")


def downgrade() -> None:
    op.drop_table("system_model_states")
    op.drop_index("ix_system_model_trades_settlement_timestamp", table_name="system_model_trades")
    op.drop_index("ix_system_model_trades_expiry_tick_sequence", table_name="system_model_trades")
    op.drop_index("ix_system_model_trades_entry_tick_sequence", table_name="system_model_trades")
    op.drop_column("system_model_trades", "exit_spot")
    op.drop_column("system_model_trades", "expected_profit_ratio")
    op.drop_column("system_model_trades", "entry_spot")
    op.drop_column("system_model_trades", "expiry_tick_sequence")
    op.drop_column("system_model_trades", "entry_tick_sequence")
