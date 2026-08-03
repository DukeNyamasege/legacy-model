"""add dashboard query indexes for the canonical model ledger

Revision ID: 20260725_0016
Revises: 20260725_0015
"""

from alembic import op
import sqlalchemy as sa


revision = "20260725_0016"
down_revision = "20260725_0015"
branch_labels = None
depends_on = None


def _indexes(table_name: str) -> set[str]:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if table_name not in existing_tables:
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    _create_index_if_missing(
        "ix_system_model_trades_run_signal",
        "system_model_trades",
        ["run_id", "signal_timestamp"],
    )
    _create_index_if_missing(
        "ix_system_model_trades_run_open",
        "system_model_trades",
        ["run_id", "is_virtual", "outcome"],
    )


def downgrade() -> None:
    op.drop_index("ix_system_model_trades_run_open", table_name="system_model_trades")
    op.drop_index("ix_system_model_trades_run_signal", table_name="system_model_trades")
