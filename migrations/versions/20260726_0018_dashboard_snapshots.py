"""persist last-good dashboard snapshots and add accounting query indexes

Revision ID: 20260726_0018
Revises: 20260726_0017
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0018"
down_revision = "20260726_0017"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    if table_name not in _table_names():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    if "dashboard_snapshots" not in _table_names():
        op.create_table(
            "dashboard_snapshots",
            sa.Column("account_type", sa.String(10), primary_key=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("snapshot_version", sa.Integer(), nullable=False),
            sa.Column("source_watermark", sa.JSON(), nullable=False),
        )
    if "ix_system_model_trades_run_signal_timestamp" not in _indexes("system_model_trades"):
        op.create_index(
            "ix_system_model_trades_run_signal_timestamp",
            "system_model_trades",
            ["run_id", "signal_timestamp"],
        )
    # Supports the coalesced provider/purchase-time cohort query and cheaply
    # rejects open/invalid contracts before the joins. PostgreSQL uses the
    # partial predicate; SQLite test databases retain the same column index.
    settled_predicate = sa.text(
        "settlement_time IS NOT NULL AND outcome IN ('WIN', 'LOSS') "
        "AND buy_price IS NOT NULL AND buy_price > 0 "
        "AND payout IS NOT NULL AND profit IS NOT NULL"
    )
    if "ix_trades_observed_settled_purchase" not in _indexes("trades"):
        op.create_index(
            "ix_trades_observed_settled_purchase",
            "trades",
            [sa.text("COALESCE(provider_purchase_time, purchase_time)"), "managed_account_id"],
            postgresql_where=settled_predicate,
            sqlite_where=settled_predicate,
        )


def downgrade() -> None:
    op.drop_index("ix_trades_observed_settled_purchase", table_name="trades")
    op.drop_index(
        "ix_system_model_trades_run_signal_timestamp",
        table_name="system_model_trades",
    )
    op.drop_table("dashboard_snapshots")
