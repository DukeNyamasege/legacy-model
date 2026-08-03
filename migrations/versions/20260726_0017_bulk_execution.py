"""add bulk execution audit persistence

Revision ID: 20260726_0017
Revises: 20260725_0016
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0017"
down_revision = "20260725_0016"
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


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    if "bulk_execution_batches" not in _table_names():
        op.create_table(
            "bulk_execution_batches",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("signal_id", sa.String(36), nullable=False),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("test_runs.id"), nullable=False),
            sa.Column("account_type", sa.String(10), nullable=False),
            sa.Column("martingale_enabled", sa.Boolean(), nullable=False),
            sa.Column("stake", sa.Float(), nullable=False),
            sa.Column("shard_index", sa.Integer(), nullable=False),
            sa.Column("account_count", sa.Integer(), nullable=False),
            sa.Column("leader_managed_account_id", sa.Integer(), sa.ForeignKey("managed_accounts.id")),
            sa.Column("pre_trade_profit_ratio", sa.Float(), nullable=False),
            sa.Column("request_metadata", sa.JSON(), nullable=False),
            sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("response_received_at", sa.DateTime(timezone=True)),
            sa.Column("latency_ms", sa.Float()),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("successful_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("unique_start_time_count", sa.Integer()),
            sa.Column("unique_entry_spot_count", sa.Integer()),
            sa.Column("unique_expiry_time_count", sa.Integer()),
            sa.Column("unique_outcome_count", sa.Integer()),
            sa.Column("first_purchase_timestamp", sa.DateTime(timezone=True)),
            sa.Column("last_purchase_timestamp", sa.DateTime(timezone=True)),
            sa.Column("execution_spread_ms", sa.Float()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    _create_index_if_missing("ix_bulk_batches_signal", "bulk_execution_batches", ["signal_id"])
    _create_index_if_missing("ix_bulk_batches_run_status", "bulk_execution_batches", ["run_id", "status"])
    if "bulk_execution_members" not in _table_names():
        op.create_table(
            "bulk_execution_members",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("batch_id", sa.String(36), sa.ForeignKey("bulk_execution_batches.id"), nullable=False),
            sa.Column("managed_account_id", sa.Integer(), sa.ForeignKey("managed_accounts.id"), nullable=False),
            sa.Column("account_id_masked", sa.String(50), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("contract_id", sa.String(100)),
            sa.Column("trade_id", sa.String(100)),
            sa.Column("buy_price", sa.Float()),
            sa.Column("payout", sa.Float()),
            sa.Column("profit", sa.Float()),
            sa.Column("outcome", sa.String(20)),
            sa.Column("provider_start_time", sa.DateTime(timezone=True)),
            sa.Column("provider_expiry_time", sa.DateTime(timezone=True)),
            sa.Column("entry_spot", sa.Float()),
            sa.Column("error_code", sa.String(80)),
            sa.Column("error_message", sa.String(240)),
            sa.Column("purchase_timestamp", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("batch_id", "managed_account_id", name="uq_bulk_member_batch_account"),
        )
    _create_index_if_missing("ix_bulk_members_batch", "bulk_execution_members", ["batch_id"])
    _create_index_if_missing("ix_bulk_members_account", "bulk_execution_members", ["managed_account_id"])
    trade_columns = _columns("trades")
    if "managed_account_id" not in trade_columns or "bulk_batch_id" not in trade_columns:
        with op.batch_alter_table("trades") as batch_op:
            if "managed_account_id" not in trade_columns:
                batch_op.add_column(sa.Column("managed_account_id", sa.Integer()))
            if "bulk_batch_id" not in trade_columns:
                batch_op.add_column(sa.Column("bulk_batch_id", sa.String(36)))
    _create_index_if_missing("ix_trades_managed_account_id", "trades", ["managed_account_id"])
    _create_index_if_missing("ix_trades_bulk_batch_id", "trades", ["bulk_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_trades_bulk_batch_id", table_name="trades")
    op.drop_index("ix_trades_managed_account_id", table_name="trades")
    with op.batch_alter_table("trades") as batch_op:
        batch_op.drop_constraint("fk_trades_bulk_batch_id", type_="foreignkey")
        batch_op.drop_constraint("fk_trades_managed_account_id", type_="foreignkey")
        batch_op.drop_column("bulk_batch_id")
        batch_op.drop_column("managed_account_id")
    op.drop_table("bulk_execution_members")
    op.drop_table("bulk_execution_batches")
