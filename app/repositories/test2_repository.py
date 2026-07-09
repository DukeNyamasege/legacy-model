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
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }

    def set_managed_account_enabled(self, account_id: int, enabled: bool) -> dict[str, Any]:
        return self.update_managed_account(account_id, enabled=bool(enabled))

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
                "created_at": account.created_at,
                "updated_at": account.updated_at,
                "expires_at": row.expires_at,
            }

    def delete_client_session(self, session_hash: str) -> None:
        with self.database.session() as session:
            row = session.get(ClientSession, str(session_hash))
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
                ).where(Trade.account_id_masked == masked)
            ).one()
            return {
                "account": masked,
                "balance": float(snapshot.balance if snapshot else 0.0),
                "currency": str(snapshot.currency if snapshot else "USD"),
                "status": str(snapshot.status if snapshot else "linked"),
                "updated_at": snapshot.updated_at.isoformat() if snapshot else None,
                "trades": int(trade_row.trades or 0),
                "wins": int(trade_row.wins or 0),
                "losses": int(trade_row.losses or 0),
                "profit": float(trade_row.profit or 0.0),
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
            status = state.status if state else "UNKNOWN"
            runtime_guard_state = self._runtime_guard_state(status)
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
            open_trade_rows = session.scalars(
                select(Trade)
                .where(Trade.settlement_time.is_(None))
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
                )
                .group_by(Trade.account_id_masked)
                .order_by(Trade.account_id_masked)
            ).all()
            trade_stats_by_account = {
                str(row.account_id_masked): {
                    "trades": int(row.trades or 0),
                    "wins": int(row.wins or 0),
                    "losses": int(row.losses or 0),
                }
                for row in account_trade_rows
            }
            settled_trades = session.scalars(
                select(Trade)
                .where(Trade.settlement_time.is_not(None))
                .order_by(Trade.settlement_time.asc(), Trade.id.asc())
            ).all()
            longest_win_streak = 0
            longest_loss_streak = 0
            current_outcome = ""
            current_length = 0
            for trade in settled_trades:
                outcome = str(trade.outcome or "").upper()
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
                "net_profit": state.total_profit if state else 0.0,
                "maximum_drawdown": state.current_drawdown if state else 0.0,
                "total_traders": int(total_managed_accounts or len(accounts)),
                "accounts": [
                    {
                        "account": account.account_id_masked,
                        "balance": account.balance,
                        "currency": account.currency,
                        "status": account.status,
                        "updated_at": account.updated_at.isoformat(),
                        **trade_stats_by_account.get(
                            account.account_id_masked,
                            {"trades": 0, "wins": 0, "losses": 0},
                        ),
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
                    "closure_summary": (
                        f"{trade.outcome} exit digit {trade.exit_digit}"
                        if trade.settlement_time is not None and trade.exit_digit is not None
                        else (
                            f"{trade.outcome} settled"
                            if trade.settlement_time is not None
                            else "Awaiting settlement"
                        )
                    ),
                    "age_seconds": max(
                        0,
                        int(
                            (
                                (trade.settlement_time or utc_now()) - trade.purchase_time
                            ).total_seconds()
                        ),
                    ),
                    "aligned_with_signal": trade.aligned_with_signal,
                }
                for trade in trades
            ]

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
