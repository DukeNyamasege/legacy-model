"""Add RF-DIR5 shadow, directional signal, virtual guard, and account risk ledgers."""

from alembic import op
import sqlalchemy as sa


revision = "20260717_0007"
down_revision = "20260716_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    required = {
        "directional_signals",
        "shadow_contracts",
        "virtual_guard_state",
        "account_risk_states",
    }
    # The original baseline migration calls Base.metadata.create_all(), so a fresh
    # database already contains newly mapped tables before this revision runs.
    if required.issubset(existing):
        return
    if required.intersection(existing):
        raise RuntimeError("RF-DIR5 migration found a partially-created schema")
    op.create_table(
        "directional_signals",
        sa.Column("signal_id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("test_runs.id"), nullable=False),
        sa.Column("strategy_version", sa.String(length=100), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("contract_type", sa.String(length=10), nullable=False),
        sa.Column("duration_ticks", sa.Integer(), nullable=False),
        sa.Column("signal_epoch", sa.Integer(), nullable=False),
        sa.Column("signal_tick_id", sa.String(length=100), nullable=False),
        sa.Column("tick_sequence", sa.Integer(), nullable=False),
        sa.Column("reference_entry_quote", sa.Float(), nullable=False),
        sa.Column("analysis_quotes", sa.JSON(), nullable=False),
        sa.Column("movements", sa.JSON(), nullable=False),
        sa.Column("feature_values", sa.JSON(), nullable=False),
        sa.Column("quality_score", sa.Integer(), nullable=False),
        sa.Column("validated_edge", sa.Float(), nullable=True),
        sa.Column("selected_for_execution", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("execution_decision", sa.String(length=50), server_default="PENDING", nullable=False),
        sa.Column("execution_reason", sa.String(length=200), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_directional_signals_run_id", "directional_signals", ["run_id"])
    op.create_index("ix_directional_signals_strategy_version", "directional_signals", ["strategy_version"])
    op.create_index("ix_directional_signals_symbol", "directional_signals", ["symbol"])
    op.create_index("ix_directional_signals_direction", "directional_signals", ["direction"])

    op.create_table(
        "shadow_contracts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("test_runs.id"), nullable=False),
        sa.Column("signal_id", sa.String(length=36), sa.ForeignKey("directional_signals.signal_id"), nullable=False),
        sa.Column("strategy_version", sa.String(length=100), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("duration_ticks", sa.Integer(), nullable=False),
        sa.Column("entry_tick_sequence", sa.Integer(), nullable=False),
        sa.Column("expiry_tick_sequence", sa.Integer(), nullable=False),
        sa.Column("entry_quote", sa.Float(), nullable=False),
        sa.Column("expiry_quote", sa.Float(), nullable=True),
        sa.Column("proposal_ask_price", sa.Float(), nullable=True),
        sa.Column("proposal_payout", sa.Float(), nullable=True),
        sa.Column("break_even_probability", sa.Float(), nullable=True),
        sa.Column("hypothetical_profit", sa.Float(), nullable=True),
        sa.Column("outcome", sa.String(length=10), server_default="OPEN", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="OPEN", nullable=False),
        sa.Column("execution_state", sa.String(length=30), server_default="SHADOW", nullable=False),
        sa.Column("execution_reason", sa.String(length=200), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("signal_id", "duration_ticks", name="uq_shadow_signal_duration"),
    )
    for column in ("run_id", "signal_id", "strategy_version", "symbol", "direction", "duration_ticks", "expiry_tick_sequence"):
        op.create_index(f"ix_shadow_contracts_{column}", "shadow_contracts", [column])

    op.create_table(
        "virtual_guard_state",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("test_runs.id"), primary_key=True),
        sa.Column("state", sa.String(length=40), server_default="DEMO_LIVE", nullable=False),
        sa.Column("active_signal_id", sa.String(length=36), server_default="", nullable=False),
        sa.Column("active_shadow_duration", sa.Integer(), server_default="0", nullable=False),
        sa.Column("demo_losses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("virtual_wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "account_risk_states",
        sa.Column("managed_account_id", sa.Integer(), sa.ForeignKey("managed_accounts.id"), primary_key=True),
        sa.Column("trading_day", sa.String(length=10), server_default="", nullable=False),
        sa.Column("daily_start_balance", sa.Float(), server_default="0", nullable=False),
        sa.Column("session_profit", sa.Float(), server_default="0", nullable=False),
        sa.Column("consecutive_losses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("equity_high_water", sa.Float(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("account_risk_states")
    op.drop_table("virtual_guard_state")
    for column in ("expiry_tick_sequence", "duration_ticks", "direction", "symbol", "strategy_version", "signal_id", "run_id"):
        op.drop_index(f"ix_shadow_contracts_{column}", table_name="shadow_contracts")
    op.drop_table("shadow_contracts")
    for column in ("direction", "symbol", "strategy_version", "run_id"):
        op.drop_index(f"ix_directional_signals_{column}", table_name="directional_signals")
    op.drop_table("directional_signals")
