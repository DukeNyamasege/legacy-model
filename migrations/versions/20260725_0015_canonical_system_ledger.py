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


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _table_names():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    if table_name not in _table_names():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    _add_column_if_missing("system_model_trades", sa.Column("entry_tick_sequence", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing("system_model_trades", sa.Column("expiry_tick_sequence", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing("system_model_trades", sa.Column("entry_spot", sa.Float(), nullable=False, server_default="0"))
    _add_column_if_missing("system_model_trades", sa.Column("exit_spot", sa.Float(), nullable=True))
    _add_column_if_missing("system_model_trades", sa.Column("expected_profit_ratio", sa.Float(), nullable=False, server_default="0.90"))
    _create_index_if_missing("ix_system_model_trades_entry_tick_sequence", "system_model_trades", ["entry_tick_sequence"])
    _create_index_if_missing("ix_system_model_trades_expiry_tick_sequence", "system_model_trades", ["expiry_tick_sequence"])
    _create_index_if_missing("ix_system_model_trades_settlement_timestamp", "system_model_trades", ["settlement_timestamp"])
    if "system_model_states" not in _table_names():
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
