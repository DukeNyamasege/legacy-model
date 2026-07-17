from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    model_version: Mapped[str] = mapped_column(String(100))
    strategy_version: Mapped[str] = mapped_column(String(100))
    configuration_hash: Mapped[str] = mapped_column(String(64))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    environment: Mapped[str] = mapped_column(String(10))
    account_id_masked: Mapped[str] = mapped_column(String(50), default="")
    symbol: Mapped[str] = mapped_column(String(30))
    stake: Mapped[float] = mapped_column(Float)
    barrier: Mapped[str] = mapped_column(String(10))
    trigger: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str] = mapped_column(Text, default="")


class Tick(Base):
    __tablename__ = "ticks"
    __table_args__ = (
        UniqueConstraint("run_id", "connection_session_id", "sequence_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence_id: Mapped[int] = mapped_column(Integer, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(30))
    epoch: Mapped[int] = mapped_column(Integer, index=True)
    tick_id: Mapped[str] = mapped_column(String(100), default="")
    quote: Mapped[float] = mapped_column(Float)
    final_digit: Mapped[int] = mapped_column(Integer)
    low_high_class: Mapped[str] = mapped_column(String(4))
    received_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    connection_session_id: Mapped[str] = mapped_column(String(100), index=True)


class CandidateSignalRecord(Base):
    __tablename__ = "candidate_signals"

    signal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(30))
    contract_type: Mapped[str] = mapped_column(String(30))
    barrier: Mapped[str] = mapped_column(String(10))
    trigger_digits: Mapped[list] = mapped_column(JSON)
    trigger_name: Mapped[str] = mapped_column(String(30))
    signal_tick_epoch: Mapped[int] = mapped_column(Integer)
    signal_tick_id: Mapped[str] = mapped_column(String(100))
    signal_last_digit: Mapped[int] = mapped_column(Integer)
    generated_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    final_status: Mapped[str] = mapped_column(String(50), default="CREATED")
    connection_session_id: Mapped[str] = mapped_column(String(100))
    tick_sequence: Mapped[int] = mapped_column(Integer)
    proposal_request_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    proposal_response_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    purchase_request_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    purchase_confirmation_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    ticks_between_signal_and_purchase: Mapped[int | None] = mapped_column(Integer)


class ModelDecisionRecord(Base):
    __tablename__ = "model_decisions"

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    signal_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_signals.signal_id"), unique=True, index=True
    )
    hmm_output: Mapped[dict] = mapped_column(JSON)
    bayesian_output: Mapped[dict] = mapped_column(JSON)
    break_even_rate: Mapped[float] = mapped_column(Float)
    expected_value: Mapped[float] = mapped_column(Float)
    final_decision: Mapped[str] = mapped_column(String(50))
    rejection_reasons: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProposalRecord(Base):
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(100), index=True)
    signal_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_signals.signal_id"), unique=True, index=True
    )
    contract_type: Mapped[str] = mapped_column(String(30))
    barrier: Mapped[str] = mapped_column(String(10))
    symbol: Mapped[str] = mapped_column(String(30))
    stake: Mapped[float] = mapped_column(Float)
    payout: Mapped[float] = mapped_column(Float)
    potential_profit: Mapped[float] = mapped_column(Float)
    potential_loss: Mapped[float] = mapped_column(Float)
    break_even_probability: Mapped[float] = mapped_column(Float)
    predicted_win_probability: Mapped[float] = mapped_column(Float)
    expected_value: Mapped[float] = mapped_column(Float)
    expected_return_on_stake: Mapped[float] = mapped_column(Float)
    request_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("signal_id", "account_id_masked", name="uq_trade_signal_account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    signal_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_signals.signal_id"), index=True
    )
    contract_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    account_id_masked: Mapped[str] = mapped_column(String(50))
    purchase_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settlement_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_purchase_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_expiry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_settlement_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contract_duration: Mapped[int] = mapped_column(Integer, default=1)
    contract_duration_unit: Mapped[str] = mapped_column(String(10), default="t")
    entry_tick: Mapped[float | None] = mapped_column(Float)
    exit_tick: Mapped[float | None] = mapped_column(Float)
    exit_digit: Mapped[int | None] = mapped_column(Integer)
    buy_price: Mapped[float | None] = mapped_column(Float)
    payout: Mapped[float | None] = mapped_column(Float)
    app_markup_amount: Mapped[float | None] = mapped_column(Float)
    commission: Mapped[float | None] = mapped_column(Float)
    profit: Mapped[float | None] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(20), default="OPEN")
    cumulative_profit: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    aligned_with_signal: Mapped[bool | None] = mapped_column(Boolean)
    model_version: Mapped[str] = mapped_column(String(100))
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)


class Streak(Base):
    __tablename__ = "streaks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), index=True)
    streak_type: Mapped[str] = mapped_column(String(10))
    length: Mapped[int] = mapped_column(Integer)
    start_trade: Mapped[str] = mapped_column(String(100))
    end_trade: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BotState(Base):
    __tablename__ = "bot_state"

    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="STOPPED")
    current_sequence: Mapped[int] = mapped_column(Integer, default=0)
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    current_streak_type: Mapped[str] = mapped_column(String(10), default="")
    current_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    session_profit: Mapped[float] = mapped_column(Float, default=0.0)
    total_profit: Mapped[float] = mapped_column(Float, default=0.0)
    high_water_mark: Mapped[float] = mapped_column(Float, default=0.0)
    pause_reason: Mapped[str] = mapped_column(String(100), default="")
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    current_connection_id: Mapped[str] = mapped_column(String(100), default="")
    consecutive_wins: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_ticks_remaining: Mapped[int] = mapped_column(Integer, default=0)


class ModelArtifact(Base):
    __tablename__ = "model_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_type: Mapped[str] = mapped_column(String(30))
    model_version: Mapped[str] = mapped_column(String(100))
    storage_location: Mapped[str] = mapped_column(Text)
    artifact_metadata: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    active_status: Mapped[bool] = mapped_column(Boolean, default=True)


class TraderLease(Base):
    __tablename__ = "trader_leases"

    lease_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(100))
    host_name: Mapped[str] = mapped_column(String(200))
    process_id: Mapped[int] = mapped_column(Integer)
    deployment_id: Mapped[str] = mapped_column(String(100))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(100))
    actor: Mapped[str] = mapped_column(String(100))
    source_ip: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "account_id_masked", name="uq_run_account_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), index=True)
    account_id_masked: Mapped[str] = mapped_column(String(50))
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    status: Mapped[str] = mapped_column(String(30), default="active")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ManagedAccount(Base):
    __tablename__ = "managed_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    token_secret: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stake_amount: Mapped[float] = mapped_column(Float, default=0.50)
    take_profit: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss: Mapped[float] = mapped_column(Float, default=0.0)
    execution_status: Mapped[str] = mapped_column(String(30), default="inactive")
    execution_status_reason: Mapped[str] = mapped_column(String(160), default="")
    execution_status_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClientSession(Base):
    __tablename__ = "client_sessions"

    session_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    managed_account_id: Mapped[int] = mapped_column(ForeignKey("managed_accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OAuthLoginState(Base):
    __tablename__ = "oauth_login_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_verifier_secret: Mapped[str] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RuntimePreference(Base):
    __tablename__ = "runtime_preferences"

    preference_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    preference_value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DirectionalSignal(Base):
    __tablename__ = "directional_signals"

    signal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), index=True)
    strategy_version: Mapped[str] = mapped_column(String(100), index=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)
    contract_type: Mapped[str] = mapped_column(String(10))
    duration_ticks: Mapped[int] = mapped_column(Integer)
    signal_epoch: Mapped[int] = mapped_column(Integer)
    signal_tick_id: Mapped[str] = mapped_column(String(100))
    tick_sequence: Mapped[int] = mapped_column(Integer)
    reference_entry_quote: Mapped[float] = mapped_column(Float)
    analysis_quotes: Mapped[list] = mapped_column(JSON)
    movements: Mapped[list] = mapped_column(JSON)
    feature_values: Mapped[dict] = mapped_column(JSON)
    quality_score: Mapped[int] = mapped_column(Integer)
    validated_edge: Mapped[float | None] = mapped_column(Float)
    selected_for_execution: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_decision: Mapped[str] = mapped_column(String(50), default="PENDING")
    execution_reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ShadowContract(Base):
    __tablename__ = "shadow_contracts"
    __table_args__ = (
        UniqueConstraint("signal_id", "duration_ticks", name="uq_shadow_signal_duration"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), index=True)
    signal_id: Mapped[str] = mapped_column(
        ForeignKey("directional_signals.signal_id"), index=True
    )
    strategy_version: Mapped[str] = mapped_column(String(100), index=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)
    duration_ticks: Mapped[int] = mapped_column(Integer, index=True)
    entry_tick_sequence: Mapped[int] = mapped_column(Integer)
    expiry_tick_sequence: Mapped[int] = mapped_column(Integer, index=True)
    entry_quote: Mapped[float] = mapped_column(Float)
    expiry_quote: Mapped[float | None] = mapped_column(Float)
    proposal_ask_price: Mapped[float | None] = mapped_column(Float)
    proposal_payout: Mapped[float | None] = mapped_column(Float)
    break_even_probability: Mapped[float | None] = mapped_column(Float)
    hypothetical_profit: Mapped[float | None] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(10), default="OPEN")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    execution_state: Mapped[str] = mapped_column(String(30), default="SHADOW")
    execution_reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VirtualGuardState(Base):
    __tablename__ = "virtual_guard_state"

    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(40), default="DEMO_LIVE")
    active_signal_id: Mapped[str] = mapped_column(String(36), default="")
    active_shadow_duration: Mapped[int] = mapped_column(Integer, default=0)
    demo_losses: Mapped[int] = mapped_column(Integer, default=0)
    virtual_wins: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AccountRiskState(Base):
    __tablename__ = "account_risk_states"

    managed_account_id: Mapped[int] = mapped_column(
        ForeignKey("managed_accounts.id"), primary_key=True
    )
    trading_day: Mapped[str] = mapped_column(String(10), default="")
    daily_start_balance: Mapped[float] = mapped_column(Float, default=0.0)
    session_profit: Mapped[float] = mapped_column(Float, default=0.0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    equity_high_water: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
