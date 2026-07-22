"""Add account-level virtual loss protection state.

Revision ID: 20260722_0011
Revises: 20260718_0010
Create Date: 2026-07-22 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260722_0011"
down_revision = "20260718_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "account_risk_states",
        sa.Column("account_id_masked", sa.String(length=50), nullable=False, server_default=""),
    )
    op.add_column(
        "account_risk_states",
        sa.Column(
            "protection_mode",
            sa.String(length=40),
            nullable=False,
            server_default="NORMAL_MODE",
        ),
    )
    op.add_column(
        "account_risk_states",
        sa.Column(
            "virtual_observation_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "account_risk_states",
        sa.Column("virtual_win_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "account_risk_states",
        sa.Column("virtual_loss_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "account_risk_states",
        sa.Column(
            "current_virtual_loss_streak",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "account_risk_states",
        sa.Column("entered_virtual_mode_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "account_risk_states",
        sa.Column("recovery_pending_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_account_risk_states_account_id_masked",
        "account_risk_states",
        ["account_id_masked"],
    )

    op.create_table(
        "virtual_trades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("virtual_trade_id", sa.String(length=100), nullable=False),
        sa.Column("managed_account_id", sa.Integer(), nullable=False),
        sa.Column("account_id_masked", sa.String(length=50), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.String(length=36), nullable=False),
        sa.Column("execution_session_id", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("strategy_id", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("market", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False, server_default=""),
        sa.Column("contract_type", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("barrier", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("prediction_digit", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("duration_unit", sa.String(length=10), nullable=False, server_default="t"),
        sa.Column("signal_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_tick_sequence", sa.Integer(), nullable=False),
        sa.Column("exit_tick_sequence", sa.Integer(), nullable=False),
        sa.Column("entry_tick_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exit_tick_epoch", sa.Integer(), nullable=True),
        sa.Column("entry_spot", sa.Float(), nullable=False),
        sa.Column("exit_spot", sa.Float(), nullable=True),
        sa.Column("actual_last_digit", sa.Integer(), nullable=True),
        sa.Column("configured_stake", sa.Float(), nullable=False, server_default="0"),
        sa.Column("simulated_stake", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expected_payout", sa.Float(), nullable=True),
        sa.Column("result", sa.String(length=30), nullable=False, server_default="OPEN"),
        sa.Column("reason", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("amount_charged", sa.Float(), nullable=False, server_default="0"),
        sa.Column("actual_profit_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("actual_payout", sa.Float(), nullable=False, server_default="0"),
        sa.Column("recovery_debt_change", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["managed_account_id"], ["managed_accounts.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["test_runs.id"]),
        sa.ForeignKeyConstraint(["signal_id"], ["directional_signals.signal_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "managed_account_id",
            "signal_id",
            name="uq_virtual_trade_account_signal",
        ),
    )
    op.create_index("ix_virtual_trades_virtual_trade_id", "virtual_trades", ["virtual_trade_id"], unique=True)
    op.create_index("ix_virtual_trades_managed_account_id", "virtual_trades", ["managed_account_id"])
    op.create_index("ix_virtual_trades_account_id_masked", "virtual_trades", ["account_id_masked"])
    op.create_index("ix_virtual_trades_run_id", "virtual_trades", ["run_id"])
    op.create_index("ix_virtual_trades_signal_id", "virtual_trades", ["signal_id"])
    op.create_index("ix_virtual_trades_market", "virtual_trades", ["market"])
    op.create_index("ix_virtual_trades_result", "virtual_trades", ["result"])
    op.create_index("ix_virtual_trades_entry_tick_sequence", "virtual_trades", ["entry_tick_sequence"])
    op.create_index("ix_virtual_trades_exit_tick_sequence", "virtual_trades", ["exit_tick_sequence"])


def downgrade() -> None:
    op.drop_index("ix_virtual_trades_exit_tick_sequence", table_name="virtual_trades")
    op.drop_index("ix_virtual_trades_entry_tick_sequence", table_name="virtual_trades")
    op.drop_index("ix_virtual_trades_result", table_name="virtual_trades")
    op.drop_index("ix_virtual_trades_market", table_name="virtual_trades")
    op.drop_index("ix_virtual_trades_signal_id", table_name="virtual_trades")
    op.drop_index("ix_virtual_trades_run_id", table_name="virtual_trades")
    op.drop_index("ix_virtual_trades_account_id_masked", table_name="virtual_trades")
    op.drop_index("ix_virtual_trades_managed_account_id", table_name="virtual_trades")
    op.drop_index("ix_virtual_trades_virtual_trade_id", table_name="virtual_trades")
    op.drop_table("virtual_trades")
    op.drop_index("ix_account_risk_states_account_id_masked", table_name="account_risk_states")
    op.drop_column("account_risk_states", "recovery_pending_since")
    op.drop_column("account_risk_states", "entered_virtual_mode_at")
    op.drop_column("account_risk_states", "current_virtual_loss_streak")
    op.drop_column("account_risk_states", "virtual_loss_count")
    op.drop_column("account_risk_states", "virtual_win_count")
    op.drop_column("account_risk_states", "virtual_observation_count")
    op.drop_column("account_risk_states", "protection_mode")
    op.drop_column("account_risk_states", "account_id_masked")
