from __future__ import annotations

import hashlib
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update

from app.config import Test2Config
from app.database import Database
from app.models import (
    AccountSnapshot,
    AuditEvent,
    BotState,
    CandidateSignalRecord,
    ModelArtifact,
    ModelDecisionRecord,
    ProposalRecord,
    TestRun,
    Tick,
    Trade,
    TraderLease,
    utc_now,
)
from app.model.bayesian_probability import BayesianSnapshot
from app.model.hmm_regime import HmmInference
from app.strategy.decision_engine import ProposalEconomics, TradeDecision
from app.strategy.signal_detector import CandidateSignal


def mask_account_id(account_id: str) -> str:
    value = str(account_id)
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


class Test2Repository:
    def __init__(self, database: Database, config: Test2Config) -> None:
        self.database = database
        self.config = config
        self.run_id = self._ensure_run()

    def _ensure_run(self) -> int:
        config_json = json.dumps(
            self.config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        config_hash = hashlib.sha256(config_json.encode()).hexdigest()
        with self.database.session() as session:
            run = session.scalar(
                select(TestRun).where(TestRun.run_name == self.config.model.run_id)
            )
            if run is None:
                run = TestRun(
                    run_name=self.config.model.run_id,
                    model_version=self.config.model.version,
                    strategy_version=self.config.model.version,
                    configuration_hash=config_hash,
                    environment=self.config.deriv.environment,
                    symbol=self.config.strategy.symbol,
                    stake=self.config.strategy.initial_stake,
                    barrier=str(self.config.strategy.prediction),
                    trigger=self.config.signal.trigger_name,
                    notes=self.config.model.brand,
                )
                session.add(run)
                session.flush()
                session.add(BotState(run_id=run.id))
            return int(run.id)

    def record_tick(
        self,
        *,
        sequence_id: int,
        symbol: str,
        epoch: int,
        tick_id: str,
        quote: float,
        final_digit: int,
        connection_session_id: str,
    ) -> None:
        with self.database.session() as session:
            session.add(
                Tick(
                    sequence_id=sequence_id,
                    run_id=self.run_id,
                    symbol=symbol,
                    epoch=epoch,
                    tick_id=tick_id,
                    quote=quote,
                    final_digit=final_digit,
                    low_high_class="LOW" if final_digit <= 4 else "HIGH",
                    connection_session_id=connection_session_id,
                )
            )
            state = session.get(BotState, self.run_id)
            if state:
                state.current_sequence = sequence_id
                state.current_connection_id = connection_session_id
                state.last_heartbeat = utc_now()

    def recent_digits(self, limit: int = 6000) -> list[int]:
        with self.database.session() as session:
            rows = session.scalars(
                select(Tick.final_digit)
                .where(Tick.run_id == self.run_id)
                .order_by(Tick.sequence_id.desc())
                .limit(limit)
            ).all()
        return list(reversed([int(value) for value in rows]))

    def current_tick_sequence(self) -> int:
        with self.database.session() as session:
            value = session.scalar(
                select(func.max(Tick.sequence_id)).where(Tick.run_id == self.run_id)
            )
        return int(value or 0)

    def record_candidate(self, signal: CandidateSignal) -> None:
        with self.database.session() as session:
            session.add(
                CandidateSignalRecord(
                    signal_id=signal.signal_id,
                    run_id=self.run_id,
                    symbol=signal.symbol,
                    contract_type=signal.contract_type,
                    barrier=signal.barrier,
                    trigger_digits=list(signal.trigger_digits),
                    trigger_name=signal.trigger_name,
                    signal_tick_epoch=signal.signal_tick_epoch,
                    signal_tick_id=signal.signal_tick_id,
                    signal_last_digit=signal.signal_last_digit,
                    generated_timestamp=datetime.fromisoformat(signal.generated_at),
                    connection_session_id=signal.connection_session_id,
                    tick_sequence=signal.tick_sequence,
                )
            )

    def mark_signal(
        self,
        signal_id: str,
        *,
        status: str,
        stale: bool = False,
        proposal_requested: bool = False,
        proposal_received: bool = False,
        purchase_requested: bool = False,
        purchase_confirmed: bool = False,
        ticks_between: int | None = None,
    ) -> None:
        with self.database.session() as session:
            signal = session.get(CandidateSignalRecord, signal_id)
            if signal is None:
                return
            now = utc_now()
            signal.final_status = status
            signal.stale = stale
            if proposal_requested:
                signal.proposal_request_timestamp = now
            if proposal_received:
                signal.proposal_response_timestamp = now
            if purchase_requested:
                signal.purchase_request_timestamp = now
            if purchase_confirmed:
                signal.purchase_confirmation_timestamp = now
            if ticks_between is not None:
                signal.ticks_between_signal_and_purchase = ticks_between

    def consume_signal(self, signal_id: str) -> bool:
        with self.database.session() as session:
            result = session.execute(
                update(CandidateSignalRecord)
                .where(
                    CandidateSignalRecord.signal_id == signal_id,
                    CandidateSignalRecord.consumed.is_(False),
                )
                .values(consumed=True, final_status="PURCHASE_REQUESTED")
            )
            return result.rowcount == 1

    def record_proposal(
        self, signal: CandidateSignal, economics: ProposalEconomics
    ) -> None:
        now = utc_now()
        latency = max(0.0, economics.received_monotonic - economics.requested_monotonic)
        with self.database.session() as session:
            session.add(
                ProposalRecord(
                    proposal_id=economics.proposal_id,
                    signal_id=signal.signal_id,
                    contract_type=signal.contract_type,
                    barrier=signal.barrier,
                    symbol=signal.symbol,
                    stake=economics.stake,
                    payout=economics.payout,
                    potential_profit=economics.potential_profit,
                    potential_loss=economics.potential_loss,
                    break_even_probability=economics.break_even_probability,
                    predicted_win_probability=economics.predicted_win_probability,
                    expected_value=economics.expected_value,
                    expected_return_on_stake=economics.expected_return_on_stake,
                    request_timestamp=now - timedelta(seconds=latency),
                    response_timestamp=now,
                )
            )

    def record_decision(
        self,
        decision: TradeDecision,
        *,
        hmm: HmmInference,
        bayesian: BayesianSnapshot,
    ) -> None:
        with self.database.session() as session:
            session.add(
                ModelDecisionRecord(
                    decision_id=decision.decision_id,
                    signal_id=decision.signal_id,
                    hmm_output=hmm.to_dict(),
                    bayesian_output=bayesian.to_dict(),
                    break_even_rate=decision.break_even_probability,
                    expected_value=decision.expected_value,
                    final_decision=decision.final_action,
                    rejection_reasons=decision.rejection_reasons,
                )
            )

    def register_purchase(
        self,
        *,
        signal_id: str,
        contract_id: str,
        transaction_id: str,
        account_id: str,
        purchase_time: datetime,
        aligned_with_signal: bool,
    ) -> None:
        with self.database.session() as session:
            session.add(
                Trade(
                    trade_id=transaction_id or contract_id,
                    signal_id=signal_id,
                    contract_id=contract_id,
                    account_id_masked=mask_account_id(account_id),
                    purchase_time=purchase_time,
                    aligned_with_signal=aligned_with_signal,
                    model_version=self.config.model.version,
                )
            )

    def settle_trade(
        self,
        *,
        contract_id: str,
        profit: float,
        outcome: str,
        entry_tick: float | None,
        exit_tick: float | None,
        exit_digit: int | None,
    ) -> bool:
        with self.database.session() as session:
            trade = session.scalar(
                select(Trade).where(Trade.contract_id == str(contract_id)).with_for_update()
            )
            if trade is None or trade.settlement_time is not None:
                return False
            state = session.get(BotState, self.run_id)
            if state is None:
                raise RuntimeError("Missing Test 2 bot state")
            state.total_profit += profit
            state.session_profit += profit
            state.high_water_mark = max(state.high_water_mark, state.total_profit)
            state.current_drawdown = state.high_water_mark - state.total_profit
            if outcome == "win":
                state.consecutive_wins += 1
                state.consecutive_losses = 0
            else:
                state.consecutive_losses += 1
                state.consecutive_wins = 0
            state.last_heartbeat = utc_now()
            trade.settlement_time = utc_now()
            trade.profit = profit
            trade.outcome = outcome.upper()
            trade.entry_tick = entry_tick
            trade.exit_tick = exit_tick
            trade.exit_digit = exit_digit
            trade.cumulative_profit = state.total_profit
            trade.drawdown = state.current_drawdown
            return True

    def completed_outcomes(self) -> tuple[int, int]:
        with self.database.session() as session:
            wins = session.scalar(
                select(func.count()).select_from(Trade).where(Trade.outcome == "WIN")
            )
            losses = session.scalar(
                select(func.count()).select_from(Trade).where(Trade.outcome == "LOSS")
            )
        return int(wins or 0), int(losses or 0)

    def unresolved_contracts(self) -> list[Trade]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(Trade).where(Trade.settlement_time.is_(None))
                ).all()
            )

    def set_status(self, status: str, pause_reason: str = "") -> None:
        with self.database.session() as session:
            state = session.get(BotState, self.run_id)
            if state:
                state.status = status
                state.pause_reason = pause_reason
                state.last_heartbeat = utc_now()

    def heartbeat(self, connection_id: str = "") -> None:
        with self.database.session() as session:
            state = session.get(BotState, self.run_id)
            if state:
                state.last_heartbeat = utc_now()
                if connection_id:
                    state.current_connection_id = connection_id

    def update_account_balance(
        self,
        *,
        account_id: str,
        balance: float,
        currency: str,
        status: str = "active",
    ) -> None:
        masked = mask_account_id(account_id)
        with self.database.session() as session:
            row = session.scalar(
                select(AccountSnapshot).where(
                    AccountSnapshot.run_id == self.run_id,
                    AccountSnapshot.account_id_masked == masked,
                )
            )
            if row is None:
                row = AccountSnapshot(
                    run_id=self.run_id,
                    account_id_masked=masked,
                )
                session.add(row)
            row.balance = float(balance)
            row.currency = str(currency or "USD")
            row.status = str(status or "active")
            row.updated_at = utc_now()

    def control_state(self) -> tuple[str, str]:
        with self.database.session() as session:
            state = session.get(BotState, self.run_id)
            return (state.status, state.pause_reason) if state else ("STOPPED", "")

    def summary(self) -> dict[str, Any]:
        with self.database.session() as session:
            state = session.get(BotState, self.run_id)
            candidates = session.scalar(
                select(func.count()).select_from(CandidateSignalRecord).where(
                    CandidateSignalRecord.run_id == self.run_id
                )
            )
            purchased = session.scalar(select(func.count()).select_from(Trade))
            wins = session.scalar(
                select(func.count()).select_from(Trade).where(Trade.outcome == "WIN")
            )
            losses = session.scalar(
                select(func.count()).select_from(Trade).where(Trade.outcome == "LOSS")
            )
            open_trades = session.scalar(
                select(func.count()).select_from(Trade).where(
                    Trade.settlement_time.is_(None)
                )
            )
            skipped = session.scalar(
                select(func.count()).select_from(CandidateSignalRecord).where(
                    CandidateSignalRecord.run_id == self.run_id,
                    CandidateSignalRecord.final_status.like("SKIP%"),
                )
            )
            accounts = session.scalars(
                select(AccountSnapshot)
                .where(AccountSnapshot.run_id == self.run_id)
                .order_by(AccountSnapshot.account_id_masked)
            ).all()
            return {
                "run_id": self.config.model.run_id,
                "status": state.status if state else "UNKNOWN",
                "pause_reason": state.pause_reason if state else "",
                "candidate_signals": int(candidates or 0),
                "purchased_trades": int(purchased or 0),
                "open_trades": int(open_trades or 0),
                "skipped_signals": int(skipped or 0),
                "wins": int(wins or 0),
                "losses": int(losses or 0),
                "win_rate": (
                    int(wins or 0) / (int(wins or 0) + int(losses or 0))
                    if int(wins or 0) + int(losses or 0)
                    else 0.0
                ),
                "net_profit": state.total_profit if state else 0.0,
                "maximum_drawdown": state.current_drawdown if state else 0.0,
                "accounts": [
                    {
                        "account": account.account_id_masked,
                        "balance": account.balance,
                        "currency": account.currency,
                        "status": account.status,
                        "updated_at": account.updated_at.isoformat(),
                    }
                    for account in accounts
                ],
                "account_balance_total": sum(account.balance for account in accounts),
                "last_heartbeat": (
                    state.last_heartbeat.isoformat() if state and state.last_heartbeat else None
                ),
            }

    def recent_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.session() as session:
            trades = session.scalars(
                select(Trade).order_by(Trade.purchase_time.desc()).limit(limit)
            ).all()
            return [
                {
                    "contract_id": trade.contract_id,
                    "account": trade.account_id_masked,
                    "purchase_time": trade.purchase_time.isoformat(),
                    "settlement_time": (
                        trade.settlement_time.isoformat() if trade.settlement_time else None
                    ),
                    "outcome": trade.outcome,
                    "profit": trade.profit,
                    "entry_tick": trade.entry_tick,
                    "exit_tick": trade.exit_tick,
                    "exit_digit": trade.exit_digit,
                    "aligned_with_signal": trade.aligned_with_signal,
                }
                for trade in trades
            ]

    def audit(self, action: str, actor: str, source_ip: str, details: dict) -> None:
        with self.database.session() as session:
            session.add(
                AuditEvent(
                    action=action,
                    actor=actor,
                    source_ip=source_ip,
                    details=details,
                )
            )

    def record_model_artifact(
        self,
        *,
        model_type: str,
        model_version: str,
        storage_location: str,
        metadata: dict,
        checksum: str,
    ) -> None:
        with self.database.session() as session:
            session.add(
                ModelArtifact(
                    model_type=model_type,
                    model_version=model_version,
                    storage_location=storage_location,
                    artifact_metadata=metadata,
                    checksum=checksum,
                    active_status=True,
                )
            )

    def acquire_lease(
        self,
        *,
        lease_key: str,
        worker_id: str,
        host_name: str,
        process_id: int,
        deployment_id: str,
        ttl_seconds: int = 30,
    ) -> bool:
        now = utc_now()
        with self.database.session() as session:
            lease = session.scalar(
                select(TraderLease)
                .where(TraderLease.lease_key == lease_key)
                .with_for_update()
            )
            if lease and lease.worker_id != worker_id:
                expiry = lease.expires_at
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                owner_alive = True
                if lease.host_name == socket.gethostname():
                    try:
                        os.kill(lease.process_id, 0)
                    except OSError:
                        owner_alive = False
                if expiry > now and owner_alive:
                    return False
            if lease is None:
                lease = TraderLease(
                    lease_key=lease_key,
                    worker_id=worker_id,
                    host_name=host_name,
                    process_id=process_id,
                    deployment_id=deployment_id,
                    heartbeat_at=now,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                )
                session.add(lease)
            else:
                lease.worker_id = worker_id
                lease.host_name = host_name
                lease.process_id = process_id
                lease.deployment_id = deployment_id
                lease.heartbeat_at = now
                lease.expires_at = now + timedelta(seconds=ttl_seconds)
            return True

    def release_lease(self, lease_key: str, worker_id: str) -> None:
        with self.database.session() as session:
            lease = session.get(TraderLease, lease_key)
            if lease and lease.worker_id == worker_id:
                session.delete(lease)
