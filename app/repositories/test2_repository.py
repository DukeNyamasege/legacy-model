from __future__ import annotations

import hashlib
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select, update

from app.config import Test2Config
from app.database import Database
from app.models import (
    AccountSnapshot,
    AuditEvent,
    BotState,
    CandidateSignalRecord,
    ClientSession,
    ManagedAccount,
    ModelArtifact,
    ModelDecisionRecord,
    OAuthLoginState,
    ProposalRecord,
    RuntimePreference,
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

    def _current_run_signal_ids(self):
        return select(CandidateSignalRecord.signal_id).where(
            CandidateSignalRecord.run_id == self.run_id
        )

    def _current_run_trade_filter(self):
        return Trade.signal_id.in_(self._current_run_signal_ids())

    def runtime_mode(self) -> str:
        with self.database.session() as session:
            row = session.get(RuntimePreference, "trading_mode")
            value = (row.preference_value if row else self.config.deriv.environment).strip().lower()
            return value if value in {"demo", "real"} else "demo"

    def set_runtime_mode(self, mode: str) -> str:
        normalized = str(mode or "demo").strip().lower()
        if normalized not in {"demo", "real"}:
            raise ValueError("Mode must be demo or real")
        with self.database.session() as session:
            row = session.get(RuntimePreference, "trading_mode")
            if row is None:
                row = RuntimePreference(preference_key="trading_mode")
                session.add(row)
            row.preference_value = normalized
            row.updated_at = utc_now()
        return normalized

    def managed_accounts_revision(self) -> str:
        with self.database.session() as session:
            latest = session.scalar(select(func.max(ManagedAccount.updated_at)))
        return latest.isoformat() if latest else ""

    def list_managed_accounts(self) -> list[ManagedAccount]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(ManagedAccount).order_by(ManagedAccount.created_at, ManagedAccount.id)
                ).all()
            )

    def add_managed_account(
        self, *, label: str, token_secret: str, enabled: bool = True
    ) -> dict[str, Any]:
        with self.database.session() as session:
            row = ManagedAccount(
                label=str(label or "").strip()[:120],
                token_secret=str(token_secret),
                enabled=bool(enabled),
            )
            session.add(row)
            session.flush()
            return {
                "id": int(row.id),
                "label": row.label,
                "enabled": bool(row.enabled),
                "stake_amount": float(row.stake_amount),
                "take_profit": float(row.take_profit),
                "stop_loss": float(row.stop_loss),
                "execution_status": row.execution_status,
                "execution_status_reason": row.execution_status_reason,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }

    def update_managed_account(
        self,
        account_id: int,
        *,
        label: str | None = None,
        token_secret: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if row is None:
                raise ValueError(f"Managed account {account_id} not found")
            if label is not None:
                row.label = str(label or row.label or "").strip()[:120]
            if token_secret is not None:
                row.token_secret = str(token_secret)
            if enabled is not None:
                row.enabled = bool(enabled)
            row.updated_at = utc_now()
            return {
                "id": int(row.id),
                "label": row.label,
                "enabled": bool(row.enabled),
                "stake_amount": float(row.stake_amount),
                "take_profit": float(row.take_profit),
                "stop_loss": float(row.stop_loss),
                "execution_status": row.execution_status,
                "execution_status_reason": row.execution_status_reason,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }

    def managed_account(self, account_id: int) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if row is None:
                return None
            return {
                "id": int(row.id),
                "label": row.label,
                "token_secret": row.token_secret,
                "enabled": bool(row.enabled),
                "stake_amount": float(row.stake_amount),
                "take_profit": float(row.take_profit),
                "stop_loss": float(row.stop_loss),
                "execution_status": row.execution_status,
                "execution_status_reason": row.execution_status_reason,
                "execution_status_updated_at": row.execution_status_updated_at,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }

    def set_managed_account_enabled(self, account_id: int, enabled: bool) -> dict[str, Any]:
        result = self.update_managed_account(account_id, enabled=bool(enabled))
        self.set_managed_account_execution_status(
            account_id,
            "connecting" if enabled else "disabled",
            "Auto trading enabled" if enabled else "Auto trading disabled",
        )
        return result

    def update_account_execution_settings(
        self,
        account_id: int,
        *,
        stake_amount: float,
        take_profit: float,
        stop_loss: float,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if row is None:
                raise ValueError(f"Managed account {account_id} not found")
            row.stake_amount = float(stake_amount)
            row.take_profit = float(take_profit)
            row.stop_loss = float(stop_loss)
            row.updated_at = utc_now()
            return {
                "stake_amount": float(row.stake_amount),
                "take_profit": float(row.take_profit),
                "stop_loss": float(row.stop_loss),
            }

    def set_managed_account_execution_status(
        self,
        account_id: int,
        execution_status: str,
        reason: str = "",
    ) -> None:
        with self.database.session() as session:
            row = session.get(ManagedAccount, int(account_id))
            if row is None:
                return
            row.execution_status = str(execution_status or "inactive")[:30]
            row.execution_status_reason = str(reason or "")[:160]
            row.execution_status_updated_at = utc_now()

    def touch_managed_account_execution(self, account_ids: list[int]) -> None:
        normalized = sorted({int(account_id) for account_id in account_ids if account_id})
        if not normalized:
            return
        with self.database.session() as session:
            session.execute(
                update(ManagedAccount)
                .where(
                    ManagedAccount.id.in_(normalized),
                    ManagedAccount.enabled.is_(True),
                    ManagedAccount.execution_status == "active",
                )
                .values(execution_status_updated_at=utc_now())
            )

    def create_client_session(
        self, *, session_hash: str, managed_account_id: int, expires_at: datetime
    ) -> None:
        with self.database.session() as session:
            now = utc_now()
            session.query(ClientSession).filter(ClientSession.expires_at <= now).delete()
            row = ClientSession(
                session_hash=str(session_hash),
                managed_account_id=int(managed_account_id),
                expires_at=expires_at,
            )
            session.merge(row)

    def client_session_account(self, session_hash: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(ClientSession, str(session_hash))
            now = utc_now()
            if row is None:
                return None
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                session.delete(row)
                return None
            account = session.get(ManagedAccount, int(row.managed_account_id))
            if account is None:
                session.delete(row)
                return None
            row.last_seen_at = now
            return {
                "id": int(account.id),
                "label": account.label,
                "token_secret": account.token_secret,
                "enabled": bool(account.enabled),
                "stake_amount": float(account.stake_amount),
                "take_profit": float(account.take_profit),
                "stop_loss": float(account.stop_loss),
                "execution_status": account.execution_status,
                "execution_status_reason": account.execution_status_reason,
                "execution_status_updated_at": account.execution_status_updated_at,
                "created_at": account.created_at,
                "updated_at": account.updated_at,
                "expires_at": row.expires_at,
            }

    def delete_client_session(self, session_hash: str) -> None:
        with self.database.session() as session:
            row = session.get(ClientSession, str(session_hash))
            if row is not None:
                session.delete(row)

    def create_oauth_login_state(
        self,
        *,
        state_hash: str,
        code_verifier_secret: str,
        redirect_uri: str,
        expires_at: datetime,
    ) -> None:
        with self.database.session() as session:
            now = utc_now()
            session.query(OAuthLoginState).filter(
                OAuthLoginState.expires_at <= now
            ).delete()
            session.merge(
                OAuthLoginState(
                    state_hash=str(state_hash),
                    code_verifier_secret=str(code_verifier_secret),
                    redirect_uri=str(redirect_uri or ""),
                    expires_at=expires_at,
                )
            )

    def oauth_login_state(self, state_hash: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(OAuthLoginState, str(state_hash))
            now = utc_now()
            if row is None:
                return None
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                session.delete(row)
                return None
            return {
                "state_hash": row.state_hash,
                "code_verifier_secret": row.code_verifier_secret,
                "redirect_uri": row.redirect_uri,
                "expires_at": row.expires_at,
            }

    def delete_oauth_login_state(self, state_hash: str) -> None:
        with self.database.session() as session:
            row = session.get(OAuthLoginState, str(state_hash))
            if row is not None:
                session.delete(row)

    def account_summary(self, account_id: str) -> dict[str, Any]:
        masked = mask_account_id(account_id)
        with self.database.session() as session:
            snapshot = session.scalar(
                select(AccountSnapshot).where(
                    AccountSnapshot.run_id == self.run_id,
                    AccountSnapshot.account_id_masked == masked,
                )
            )
            trade_row = session.execute(
                select(
                    func.count().label("trades"),
                    func.sum(case((Trade.outcome == "WIN", 1), else_=0)).label("wins"),
                    func.sum(case((Trade.outcome == "LOSS", 1), else_=0)).label("losses"),
                    func.sum(Trade.profit).label("profit"),
                ).where(
                    Trade.account_id_masked == masked,
                    self._current_run_trade_filter(),
                )
            ).one()
            settled_rows = session.execute(
                select(Trade.outcome, Trade.settlement_time)
                .where(
                    Trade.account_id_masked == masked,
                    Trade.settlement_time.is_not(None),
                    self._current_run_trade_filter(),
                )
                .order_by(Trade.settlement_time.asc(), Trade.id.asc())
            ).all()
            longest_win_streak = 0
            longest_loss_streak = 0
            current_outcome = ""
            current_length = 0
            for settled in settled_rows:
                outcome = str(settled.outcome or "").upper()
                if outcome == current_outcome:
                    current_length += 1
                else:
                    current_outcome = outcome
                    current_length = 1
                if outcome == "WIN":
                    longest_win_streak = max(longest_win_streak, current_length)
                elif outcome == "LOSS":
                    longest_loss_streak = max(longest_loss_streak, current_length)
            open_rows = session.scalars(
                select(Trade)
                .where(
                    Trade.account_id_masked == masked,
                    Trade.settlement_time.is_(None),
                    self._current_run_trade_filter(),
                )
                .order_by(Trade.purchase_time.asc())
            ).all()
            oldest_open_trade_seconds = 0
            if open_rows:
                now = utc_now()
                oldest_open_trade_seconds = max(
                    0,
                    int(max((now - row.purchase_time).total_seconds() for row in open_rows)),
                )
            wins = int(trade_row.wins or 0)
            losses = int(trade_row.losses or 0)
            return {
                "account": masked,
                "balance": float(snapshot.balance if snapshot else 0.0),
                "currency": str(snapshot.currency if snapshot else "USD"),
                "status": str(snapshot.status if snapshot else "linked"),
                "updated_at": snapshot.updated_at.isoformat() if snapshot else None,
                "trades": int(trade_row.trades or 0),
                "wins": wins,
                "losses": losses,
                "profit": float(trade_row.profit or 0.0),
                "win_rate": wins / (wins + losses) if wins + losses else 0.0,
                "longest_win_streak": longest_win_streak,
                "longest_loss_streak": longest_loss_streak,
                "open_trades": len(open_rows),
                "oldest_open_trade_seconds": oldest_open_trade_seconds,
            }

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

    def recent_digits(
        self,
        limit: int = 6000,
        *,
        symbol: str | None = None,
    ) -> list[int]:
        with self.database.session() as session:
            query = select(Tick.final_digit).where(Tick.run_id == self.run_id)
            if symbol:
                query = query.where(Tick.symbol == symbol)
            rows = session.scalars(
                query.order_by(Tick.sequence_id.desc()).limit(limit)
            ).all()
        return list(reversed([int(value) for value in rows]))

    def current_tick_sequence(self, *, symbol: str | None = None) -> int:
        with self.database.session() as session:
            query = select(func.max(Tick.sequence_id)).where(Tick.run_id == self.run_id)
            if symbol:
                query = query.where(Tick.symbol == symbol)
            value = session.scalar(query)
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

    def signal_symbol(self, signal_id: str) -> str:
        with self.database.session() as session:
            value = session.scalar(
                select(CandidateSignalRecord.symbol).where(
                    CandidateSignalRecord.signal_id == signal_id,
                    CandidateSignalRecord.run_id == self.run_id,
                )
            )
        return str(value or "")

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
        buy_price: float | None = None,
        payout: float | None = None,
        provider_purchase_time: datetime | None = None,
        provider_start_time: datetime | None = None,
        contract_duration: int = 1,
        contract_duration_unit: str = "t",
    ) -> None:
        with self.database.session() as session:
            session.add(
                Trade(
                    trade_id=transaction_id or contract_id,
                    signal_id=signal_id,
                    contract_id=contract_id,
                    account_id_masked=mask_account_id(account_id),
                    purchase_time=purchase_time,
                    provider_purchase_time=provider_purchase_time,
                    provider_start_time=provider_start_time,
                    contract_duration=int(contract_duration),
                    contract_duration_unit=str(contract_duration_unit),
                    buy_price=buy_price,
                    payout=payout,
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
        buy_price: float | None = None,
        payout: float | None = None,
        app_markup_amount: float | None = None,
        commission: float | None = None,
        provider_purchase_time: datetime | None = None,
        provider_start_time: datetime | None = None,
        provider_expiry_time: datetime | None = None,
        provider_settlement_time: datetime | None = None,
    ) -> bool:
        with self.database.session() as session:
            trade = session.scalar(
                select(Trade)
                .where(
                    Trade.contract_id == str(contract_id),
                    self._current_run_trade_filter(),
                )
                .with_for_update()
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
            if provider_purchase_time is not None:
                trade.provider_purchase_time = provider_purchase_time
            if provider_start_time is not None:
                trade.provider_start_time = provider_start_time
            if provider_expiry_time is not None:
                trade.provider_expiry_time = provider_expiry_time
            if provider_settlement_time is not None:
                trade.provider_settlement_time = provider_settlement_time
            trade.profit = profit
            trade.outcome = outcome.upper()
            trade.entry_tick = entry_tick
            trade.exit_tick = exit_tick
            trade.exit_digit = exit_digit
            if buy_price is not None:
                trade.buy_price = buy_price
            if payout is not None:
                trade.payout = payout
            trade.app_markup_amount = app_markup_amount
            trade.commission = commission
            trade.cumulative_profit = state.total_profit
            trade.drawdown = state.current_drawdown
            return True

    def completed_outcomes(self) -> tuple[int, int]:
        """Return one outcome per fully settled copy-trade signal."""
        with self.database.session() as session:
            rows = session.execute(
                select(
                    Trade.signal_id,
                    func.sum(
                        case((Trade.settlement_time.is_(None), 1), else_=0)
                    ).label("open_count"),
                    func.sum(
                        case((Trade.outcome == "LOSS", 1), else_=0)
                    ).label("loss_count"),
                )
                .where(
                    self._current_run_trade_filter(),
                    Trade.model_version == self.config.model.version,
                )
                .group_by(Trade.signal_id)
            ).all()
        wins = sum(
            int(row.open_count or 0) == 0 and int(row.loss_count or 0) == 0
            for row in rows
        )
        losses = sum(
            int(row.open_count or 0) == 0 and int(row.loss_count or 0) > 0
            for row in rows
        )
        return int(wins), int(losses)

    def unresolved_contracts(self) -> list[Trade]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(Trade).where(
                        Trade.settlement_time.is_(None),
                        self._current_run_trade_filter(),
                    )
                ).all()
            )

    @staticmethod
    def _normalize_control_status(status: str, pause_reason: str = "") -> tuple[str, str]:
        if str(status or "").upper() == "EMERGENCY_STOP":
            return "RUNNING", ""
        return str(status or "STOPPED"), str(pause_reason or "")

    def set_status(self, status: str, pause_reason: str = "") -> None:
        status, pause_reason = self._normalize_control_status(status, pause_reason)
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
            if not state:
                return ("STOPPED", "")
            return self._normalize_control_status(state.status, state.pause_reason)

    def _runtime_guard_state(self, status: str) -> dict[str, Any]:
        guard_paused = False
        guard_reason = ""
        updated_at = ""
        shadow_outcomes: list[str] = []
        state_path = Path(self.config.files.state)
        if not state_path.is_absolute():
            state_path = Path.cwd() / state_path

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            bot_state = payload.get("bot", {}) if isinstance(payload, dict) else {}
            guard_paused = bool(bot_state.get("regime_guard_paused", False))
            guard_reason = str(bot_state.get("regime_guard_reason", ""))
            updated_at = str(bot_state.get("updated_at", ""))
            shadow_outcomes = [
                str(value).upper()
                for value in bot_state.get("shadow_outcomes", [])
                if str(value).upper() in {"WIN", "LOSS"}
            ]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        guard_paused = guard_paused and self.config.recovery.regime_guard_enabled

        shadow_sample_target = self.config.recovery.shadow_min_samples
        latest_shadow_outcomes = shadow_outcomes[-shadow_sample_target:]
        shadow_wins = sum(value == "WIN" for value in latest_shadow_outcomes)
        shadow_losses = sum(value == "LOSS" for value in latest_shadow_outcomes)
        shadow_samples = len(latest_shadow_outcomes)
        shadow_win_rate = shadow_wins / shadow_samples if shadow_samples else 0.0

        running = status == "RUNNING"
        if running and guard_paused:
            activity_mode = "learning"
            activity_label = "Learning Mode"
            activity_message = "Ai is learning market changes"
            activity_detail = "AI is studying market changes before trading resumes."
        elif running:
            activity_mode = "trading"
            activity_label = "Trading Mode"
            activity_message = "Ai trading resumed"
            activity_detail = "AI is watching for valid market entries."
        else:
            activity_mode = "idle"
            activity_label = "Standby"
            activity_message = "Ai trading stopped"
            activity_detail = "AI trading is stopped."

        return {
            "regime_guard_paused": guard_paused,
            "regime_guard_reason": guard_reason,
            "regime_guard_updated_at": updated_at,
            "shadow_latest_samples": shadow_samples,
            "shadow_latest_wins": shadow_wins,
            "shadow_latest_losses": shadow_losses,
            "shadow_latest_win_rate": shadow_win_rate,
            "shadow_required_win_rate": self.config.recovery.resume_above_shadow_win_rate,
            "ai_activity_mode": activity_mode,
            "ai_activity_label": activity_label,
            "ai_activity_message": activity_message,
            "ai_activity_detail": activity_detail,
        }

    def summary(self) -> dict[str, Any]:
        with self.database.session() as session:
            state = session.get(BotState, self.run_id)
            status, _ = (
                self._normalize_control_status(state.status, state.pause_reason)
                if state
                else ("UNKNOWN", "")
            )
            runtime_guard_state = self._runtime_guard_state(status)
            candidates = session.scalar(
                select(func.count()).select_from(CandidateSignalRecord).where(
                    CandidateSignalRecord.run_id == self.run_id
                )
            )
            run_trade_filter = self._current_run_trade_filter()
            purchased = session.scalar(
                select(func.count()).select_from(Trade).where(run_trade_filter)
            )
            wins = session.scalar(
                select(func.count()).select_from(Trade).where(
                    Trade.outcome == "WIN",
                    run_trade_filter,
                )
            )
            losses = session.scalar(
                select(func.count()).select_from(Trade).where(
                    Trade.outcome == "LOSS",
                    run_trade_filter,
                )
            )
            open_trades = session.scalar(
                select(func.count()).select_from(Trade).where(
                    Trade.settlement_time.is_(None),
                    run_trade_filter,
                )
            )
            open_trade_rows = session.scalars(
                select(Trade)
                .where(
                    Trade.settlement_time.is_(None),
                    run_trade_filter,
                )
                .order_by(Trade.purchase_time.asc())
            ).all()
            skipped = session.scalar(
                select(func.count()).select_from(CandidateSignalRecord).where(
                    CandidateSignalRecord.run_id == self.run_id,
                    CandidateSignalRecord.final_status.like("SKIP%"),
                )
            )
            total_managed_accounts = session.scalar(
                select(func.count()).select_from(ManagedAccount).where(
                    ManagedAccount.enabled.is_(True)
                )
            )
            accounts = session.scalars(
                select(AccountSnapshot)
                .where(AccountSnapshot.run_id == self.run_id)
                .order_by(AccountSnapshot.account_id_masked)
            ).all()
            account_trade_rows = session.execute(
                select(
                    Trade.account_id_masked,
                    func.count().label("trades"),
                    func.sum(case((Trade.outcome == "WIN", 1), else_=0)).label("wins"),
                    func.sum(case((Trade.outcome == "LOSS", 1), else_=0)).label("losses"),
                    func.sum(Trade.profit).label("profit"),
                )
                .where(run_trade_filter)
                .group_by(Trade.account_id_masked)
                .order_by(Trade.account_id_masked)
            ).all()
            trade_stats_by_account = {
                str(row.account_id_masked): {
                    "trades": int(row.trades or 0),
                    "wins": int(row.wins or 0),
                    "losses": int(row.losses or 0),
                    "profit": float(row.profit or 0.0),
                }
                for row in account_trade_rows
            }
            settled_trades = session.scalars(
                select(Trade)
                .where(
                    Trade.settlement_time.is_not(None),
                    run_trade_filter,
                )
                .order_by(Trade.settlement_time.asc(), Trade.id.asc())
            ).all()
            longest_win_streak = 0
            longest_loss_streak = 0
            current_outcome = ""
            current_length = 0
            computed_net_profit = 0.0
            computed_high_water_mark = 0.0
            computed_max_drawdown = 0.0
            for trade in settled_trades:
                outcome = str(trade.outcome or "").upper()
                computed_net_profit += float(trade.profit or 0.0)
                computed_high_water_mark = max(computed_high_water_mark, computed_net_profit)
                computed_max_drawdown = max(
                    computed_max_drawdown,
                    computed_high_water_mark - computed_net_profit,
                )
                if outcome == current_outcome:
                    current_length += 1
                else:
                    current_outcome = outcome
                    current_length = 1
                if outcome == "WIN":
                    longest_win_streak = max(longest_win_streak, current_length)
                elif outcome == "LOSS":
                    longest_loss_streak = max(longest_loss_streak, current_length)
            now = utc_now()
            oldest_open_trade_seconds = 0
            stale_open_trades = 0
            max_open_trade_seconds = max(1, int(self.config.trade.max_open_trade_seconds))
            if open_trade_rows:
                oldest_open_trade_seconds = max(
                    0,
                    int(
                        max(
                            (now - trade.purchase_time).total_seconds()
                            for trade in open_trade_rows
                        )
                    ),
                )
                stale_open_trades = sum(
                    (now - trade.purchase_time).total_seconds() > max_open_trade_seconds
                    for trade in open_trade_rows
                )
            master_account_id = os.getenv("COPYTRADING_MASTER_ACCOUNT_ID", "").strip()
            master_account_masked = mask_account_id(master_account_id) if master_account_id else ""
            primary_account = (
                next(
                    (
                        account
                        for account in accounts
                        if account.account_id_masked == master_account_masked
                    ),
                    None,
                )
                if master_account_masked
                else None
            )
            if primary_account is None:
                primary_account = accounts[0] if accounts else None
            return {
                "run_id": self.config.model.run_id,
                "status": status,
                "pause_reason": state.pause_reason if state else "",
                "mode": self.runtime_mode(),
                **runtime_guard_state,
                "candidate_signals": int(candidates or 0),
                "purchased_trades": int(purchased or 0),
                "open_trades": int(open_trades or 0),
                "stale_open_trades": int(stale_open_trades),
                "oldest_open_trade_seconds": int(oldest_open_trade_seconds),
                "max_open_trade_seconds": max_open_trade_seconds,
                "skipped_signals": int(skipped or 0),
                "wins": int(wins or 0),
                "losses": int(losses or 0),
                "longest_win_streak": longest_win_streak,
                "longest_loss_streak": longest_loss_streak,
                "win_rate": (
                    int(wins or 0) / (int(wins or 0) + int(losses or 0))
                    if int(wins or 0) + int(losses or 0)
                    else 0.0
                ),
                "net_profit": computed_net_profit,
                "maximum_drawdown": computed_max_drawdown,
                "total_traders": int(len(accounts) or total_managed_accounts or 0),
                "accounts": [
                    {
                        "account": account.account_id_masked,
                        "balance": account.balance,
                        "currency": account.currency,
                        "status": account.status,
                        "updated_at": account.updated_at.isoformat(),
                        **trade_stats_by_account.get(
                            account.account_id_masked,
                            {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},
                        ),
                    }
                    for account in accounts
                ],
                "primary_account": primary_account.account_id_masked if primary_account else "",
                "primary_account_balance": primary_account.balance if primary_account else 0.0,
                "primary_account_currency": primary_account.currency if primary_account else "USD",
                "account_balance_total": sum(account.balance for account in accounts),
                "last_heartbeat": (
                    state.last_heartbeat.isoformat() if state and state.last_heartbeat else None
                ),
            }

    def recent_trades(
        self,
        limit: int = 50,
        *,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        account_masked = mask_account_id(account_id) if account_id else ""
        with self.database.session() as session:
            query = (
                select(Trade, CandidateSignalRecord.symbol)
                .join(
                    CandidateSignalRecord,
                    CandidateSignalRecord.signal_id == Trade.signal_id,
                )
                .where(self._current_run_trade_filter())
            )
            if account_masked:
                query = query.where(Trade.account_id_masked == account_masked)
            trade_rows = session.execute(
                query
                .order_by(Trade.purchase_time.desc())
                .limit(limit)
            ).all()
            settlement_sla = float(self.config.trade.settlement_sla_seconds)
            results: list[dict[str, Any]] = []
            for trade, symbol in trade_rows:
                lifecycle_seconds = max(
                    0.0,
                    ((trade.settlement_time or utc_now()) - trade.purchase_time).total_seconds(),
                )
                provider_lifecycle_seconds = None
                if trade.provider_purchase_time and trade.provider_settlement_time:
                    provider_lifecycle_seconds = max(
                        0.0,
                        (
                            trade.provider_settlement_time
                            - trade.provider_purchase_time
                        ).total_seconds(),
                    )
                settlement_delivery_seconds = None
                if trade.settlement_time and trade.provider_settlement_time:
                    settlement_delivery_seconds = max(
                        0.0,
                        (
                            trade.settlement_time - trade.provider_settlement_time
                        ).total_seconds(),
                    )
                duration_value = max(1, int(trade.contract_duration or 1))
                duration_unit = str(trade.contract_duration_unit or "t")
                duration_label = (
                    f"{duration_value} tick{'s' if duration_value != 1 else ''}"
                    if duration_unit == "t"
                    else f"{duration_value}{duration_unit}"
                )
                results.append({
                    "contract_id": trade.contract_id,
                    "symbol": str(symbol),
                    "account": trade.account_id_masked,
                    "purchase_time": trade.purchase_time.isoformat(),
                    "settlement_time": (
                        trade.settlement_time.isoformat() if trade.settlement_time else None
                    ),
                    "provider_purchase_time": (
                        trade.provider_purchase_time.isoformat()
                        if trade.provider_purchase_time
                        else None
                    ),
                    "provider_start_time": (
                        trade.provider_start_time.isoformat()
                        if trade.provider_start_time
                        else None
                    ),
                    "provider_expiry_time": (
                        trade.provider_expiry_time.isoformat()
                        if trade.provider_expiry_time
                        else None
                    ),
                    "provider_settlement_time": (
                        trade.provider_settlement_time.isoformat()
                        if trade.provider_settlement_time
                        else None
                    ),
                    "contract_duration": duration_value,
                    "contract_duration_unit": duration_unit,
                    "duration_label": duration_label,
                    "outcome": trade.outcome,
                    "profit": trade.profit,
                    "buy_price": trade.buy_price,
                    "payout": trade.payout,
                    "app_markup_amount": trade.app_markup_amount,
                    "commission": trade.commission,
                    "entry_tick": trade.entry_tick,
                    "exit_tick": trade.exit_tick,
                    "exit_digit": trade.exit_digit,
                    "closure_summary": (
                        f"{trade.outcome} exit digit {trade.exit_digit}"
                        if trade.settlement_time is not None and trade.exit_digit is not None
                        else (
                            f"{trade.outcome} settled"
                            if trade.settlement_time is not None
                            else "Awaiting settlement"
                        )
                    ),
                    # Retain age_seconds for API compatibility, but no longer
                    # truncate it or present it as contractual duration.
                    "age_seconds": round(lifecycle_seconds, 3),
                    "lifecycle_seconds": round(lifecycle_seconds, 3),
                    "provider_lifecycle_seconds": (
                        round(provider_lifecycle_seconds, 3)
                        if provider_lifecycle_seconds is not None
                        else None
                    ),
                    "settlement_delivery_seconds": (
                        round(settlement_delivery_seconds, 3)
                        if settlement_delivery_seconds is not None
                        else None
                    ),
                    "settlement_sla_seconds": settlement_sla,
                    "settlement_sla_status": (
                        "OPEN"
                        if trade.settlement_time is None
                        else "MET"
                        if lifecycle_seconds <= settlement_sla
                        else "LATE"
                    ),
                    "aligned_with_signal": trade.aligned_with_signal,
                })
            return results

    def markup_summary(self, *, account_id: str) -> dict[str, Any]:
        account_masked = mask_account_id(account_id)
        with self.database.session() as session:
            row = session.execute(
                select(
                    func.count().label("contract_count"),
                    func.sum(
                        case(
                            (Trade.app_markup_amount > 0, 1),
                            else_=0,
                        )
                    ).label("confirmed_contract_count"),
                    func.sum(Trade.app_markup_amount).label("app_markup_total"),
                    func.sum(Trade.commission).label("commission_total"),
                ).where(
                    Trade.account_id_masked == account_masked,
                    Trade.settlement_time.is_not(None),
                    self._current_run_trade_filter(),
                )
            ).one()
        contract_count = int(row.contract_count or 0)
        confirmed_contract_count = int(row.confirmed_contract_count or 0)
        if contract_count == 0:
            status = "AWAITING_CONTRACT"
        elif confirmed_contract_count == contract_count:
            status = "CONFIRMED"
        elif confirmed_contract_count > 0:
            status = "PARTIAL"
        else:
            status = "NOT_CONFIRMED"
        return {
            "account": account_masked,
            "contract_count": contract_count,
            "confirmed_contract_count": confirmed_contract_count,
            "unconfirmed_contract_count": contract_count - confirmed_contract_count,
            "status": status,
            "expected_percentage": float(self.config.deriv.app_markup_percentage),
            "app_markup_total": float(row.app_markup_total or 0.0),
            "commission_total": float(row.commission_total or 0.0),
        }

    def recent_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.session() as session:
            signals = session.scalars(
                select(CandidateSignalRecord)
                .where(CandidateSignalRecord.run_id == self.run_id)
                .order_by(CandidateSignalRecord.generated_timestamp.desc())
                .limit(limit)
            ).all()
            results: list[dict[str, Any]] = []
            for signal in signals:
                decision = session.scalar(
                    select(ModelDecisionRecord).where(
                        ModelDecisionRecord.signal_id == signal.signal_id
                    )
                )
                results.append(
                    {
                        "signal_id": signal.signal_id,
                        "symbol": signal.symbol,
                        "generated_at": signal.generated_timestamp.isoformat(),
                        "trigger_name": signal.trigger_name,
                        "trigger_digits": signal.trigger_digits,
                        "final_status": signal.final_status,
                        "stale": signal.stale,
                        "consumed": signal.consumed,
                        "signal_last_digit": signal.signal_last_digit,
                        "ticks_between_signal_and_purchase": signal.ticks_between_signal_and_purchase,
                        "decision": decision.final_decision if decision else None,
                        "rejection_reasons": decision.rejection_reasons if decision else [],
                    }
                )
            return results

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
