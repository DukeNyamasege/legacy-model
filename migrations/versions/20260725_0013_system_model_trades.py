"""Add system model trade ledger for independent strategy performance tracking.

Revision ID: 20260725_0013
Revises: 20260724_0012
Create Date: 2026-07-25 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260725_0013"
down_revision = "20260724_0012"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    if table_name not in _table_names():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    if "system_model_trades" not in _table_names():
        op.create_table(
            "system_model_trades",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("test_runs.id"), nullable=False),
            sa.Column(
                "signal_id",
                sa.String(length=36),
                sa.ForeignKey("directional_signals.signal_id"),
                nullable=False,
            ),
            sa.Column("symbol", sa.String(length=30), nullable=False),
            sa.Column("direction", sa.String(length=10), nullable=False),
            sa.Column("contract_type", sa.String(length=30), nullable=False),
            sa.Column("duration_ticks", sa.Integer(), nullable=False),
            sa.Column("signal_timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("entry_timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("settlement_timestamp", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome", sa.String(length=10), nullable=False),
            sa.Column("is_virtual", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("reference_base_stake", sa.Float(), nullable=False, server_default="0.50"),
            sa.Column("fixed_stake_profit", sa.Float(), nullable=False, server_default="0"),
            sa.Column("martingale_stake", sa.Float(), nullable=False, server_default="0"),
            sa.Column("martingale_profit", sa.Float(), nullable=False, server_default="0"),
            sa.Column("martingale_level", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("recovery_debt_before", sa.Float(), nullable=False, server_default="0"),
            sa.Column("recovery_debt_after", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("signal_id", name="uq_system_model_trade_signal"),
        )
    for column in (
        "run_id",
        "signal_id",
        "symbol",
        "direction",
        "outcome",
        "is_virtual",
        "signal_timestamp",
        "settlement_timestamp",
    ):
        _create_index_if_missing(f"ix_system_model_trades_{column}", "system_model_trades", [column])


def downgrade() -> None:
    for column in (
        "signal_timestamp",
        "settlement_timestamp",
        "is_virtual",
        "outcome",
        "direction",
        "symbol",
        "signal_id",
        "run_id",
    ):
        op.drop_index(f"ix_system_model_trades_{column}", table_name="system_model_trades")
    op.drop_table("system_model_trades")
