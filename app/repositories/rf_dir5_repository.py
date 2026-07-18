from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select

from app.model.bayesian_probability import BayesianGroupKey
from app.models import (
    AccountRiskState,
    DirectionalSignal,
    ShadowContract,
    VirtualGuardState,
    utc_now,
)
from app.strategy.rise_fall_strategy import SignalEvent, shadow_outcome


@dataclass(frozen=True, slots=True)
class StakePlan:
    stake: float | None
    reason: str = ""
    is_recovery: bool = False
    recovery_debt: float = 0.0
    required_recovery_stake: float = 0.0


class RFDir5Repository:
    def __init__(self, base_repository: Any) -> None:
        self.base = base_repository
        self.database = base_repository.database
        self.run_id = base_repository.run_id
        with self.database.session() as session:
            if session.get(VirtualGuardState, self.run_id) is None:
                session.add(VirtualGuardState(run_id=self.run_id, state="DEMO_LIVE"))

    def record_signal(self, signal: SignalEvent) -> None:
        self.base.record_candidate(signal)
        with self.database.session() as session:
            session.add(
                DirectionalSignal(
                    signal_id=signal.signal_id,
                    run_id=self.run_id,
                    strategy_version=signal.strategy_version,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    contract_type=signal.contract_type,
                    duration_ticks=signal.duration_ticks,
                    signal_epoch=signal.signal_tick_epoch,
                    signal_tick_id=signal.signal_tick_id,
                    tick_sequence=signal.tick_sequence,
                    reference_entry_quote=float(signal.reference_entry_quote),
                    analysis_quotes=[str(value) for value in signal.features.analysis_quotes],
                    movements=[str(value) for value in signal.features.movements],
                    feature_values=signal.features.to_dict(),
                    quality_score=signal.quality_score,
                )
            )

    def create_shadow_contracts(
        self,
        signal: SignalEvent,
        durations: tuple[int, ...],
    ) -> None:
        with self.database.session() as session:
            for duration in durations:
                session.add(
                    ShadowContract(
                        run_id=self.run_id,
                        signal_id=signal.signal_id,
                        strategy_version=signal.strategy_version,
                        symbol=signal.symbol,
                        direction=signal.direction,
                        duration_ticks=int(duration),
                        entry_tick_sequence=signal.tick_sequence,
                        expiry_tick_sequence=signal.tick_sequence + int(duration),
                        entry_quote=float(signal.reference_entry_quote),
                        execution_state="SHADOW",
                    )
                )

    def update_shadow_proposal(
        self,
        signal_id: str,
        duration_ticks: int,
        *,
        ask_price: float,
        payout: float,
        break_even_probability: float,
    ) -> None:
        with self.database.session() as session:
            row = session.scalar(
                select(ShadowContract).where(
                    ShadowContract.run_id == self.run_id,
                    ShadowContract.signal_id == signal_id,
                    ShadowContract.duration_ticks == int(duration_ticks),
                )
            )
            if row is not None:
                row.proposal_ask_price = float(ask_price)
                row.proposal_payout = float(payout)
                row.break_even_probability = float(break_even_probability)

    def settle_due_shadows(
        self,
        *,
        symbol: str,
        tick_sequence: int,
        expiry_quote: Decimal,
    ) -> list[dict[str, Any]]:
        settled: list[dict[str, Any]] = []
        with self.database.session() as session:
            rows = session.scalars(
                select(ShadowContract)
                .where(
                    ShadowContract.run_id == self.run_id,
                    ShadowContract.symbol == symbol,
                    ShadowContract.status == "OPEN",
                    ShadowContract.expiry_tick_sequence <= int(tick_sequence),
                )
                .with_for_update()
            ).all()
            for row in rows:
                result = shadow_outcome(
                    row.direction,
                    Decimal(str(row.entry_quote)),
                    expiry_quote,
                )
                if row.proposal_ask_price is not None and row.proposal_payout is not None:
                    hypothetical = (
                        row.proposal_payout - row.proposal_ask_price
                        if result == "WIN"
                        else -row.proposal_ask_price
                    )
                else:
                    hypothetical = None
                row.expiry_quote = float(expiry_quote)
                row.outcome = result
                row.hypothetical_profit = hypothetical
                row.status = "SETTLED"
                row.settled_at = utc_now()
                settled.append(
                    {
                        "signal_id": row.signal_id,
                        "strategy_version": row.strategy_version,
                        "symbol": row.symbol,
                        "direction": row.direction,
                        "duration_ticks": row.duration_ticks,
                        "outcome": result,
                        "execution_state": row.execution_state,
                    }
                )
        return settled

    def set_signal_decision(
        self,
        signal_id: str,
        decision: str,
        reason: str,
        *,
        selected: bool = False,
        validated_edge: float | None = None,
    ) -> None:
        with self.database.session() as session:
            row = session.get(DirectionalSignal, signal_id)
            if row is not None:
                row.execution_decision = decision
                row.execution_reason = reason[:200]
                row.selected_for_execution = bool(selected)
                row.validated_edge = validated_edge
            shadows = session.scalars(
                select(ShadowContract).where(
                    ShadowContract.run_id == self.run_id,
                    ShadowContract.signal_id == signal_id,
                )
            ).all()
            for shadow in shadows:
                shadow.execution_reason = reason[:200]

    def shadow_group_counts(self, key: BayesianGroupKey) -> tuple[int, int]:
        with self.database.session() as session:
            row = session.execute(
                select(
                    func.sum(case((ShadowContract.outcome == "WIN", 1), else_=0)),
                    func.sum(case((ShadowContract.outcome == "LOSS", 1), else_=0)),
                ).where(
                    ShadowContract.run_id == self.run_id,
                    ShadowContract.strategy_version == key.strategy_version,
                    ShadowContract.symbol == key.market,
                    ShadowContract.direction == key.direction,
                    ShadowContract.duration_ticks == key.duration_ticks,
                    ShadowContract.status == "SETTLED",
                )
            ).one()
        return int(row[0] or 0), int(row[1] or 0)

    def shadow_groups(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.execute(
                select(
                    ShadowContract.strategy_version,
                    ShadowContract.symbol,
                    ShadowContract.direction,
                    ShadowContract.duration_ticks,
                    func.sum(case((ShadowContract.outcome == "WIN", 1), else_=0)),
                    func.sum(case((ShadowContract.outcome == "LOSS", 1), else_=0)),
                    func.sum(ShadowContract.hypothetical_profit),
                )
                .where(
                    ShadowContract.run_id == self.run_id,
                    ShadowContract.status == "SETTLED",
                )
                .group_by(
                    ShadowContract.strategy_version,
                    ShadowContract.symbol,
                    ShadowContract.direction,
                    ShadowContract.duration_ticks,
                )
                .order_by(
                    ShadowContract.symbol,
                    ShadowContract.direction,
                    ShadowContract.duration_ticks,
                )
            ).all()
        return [
            {
                "strategy_version": row[0],
                "symbol": row[1],
                "direction": row[2],
                "duration_ticks": row[3],
                "wins": int(row[4] or 0),
                "losses": int(row[5] or 0),
                "profit": float(row[6] or 0.0),
            }
            for row in rows
        ]

    def guard_state(self) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(VirtualGuardState, self.run_id)
            if row is None:
                row = VirtualGuardState(run_id=self.run_id, state="DEMO_LIVE")
                session.add(row)
                session.flush()
            return {
                "state": row.state,
                "active_signal_id": row.active_signal_id,
                "active_shadow_duration": row.active_shadow_duration,
                "demo_losses": row.demo_losses,
                "virtual_wins": row.virtual_wins,
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            }

    def reset_guard(self) -> None:
        with self.database.session() as session:
            row = session.get(VirtualGuardState, self.run_id, with_for_update=True)
            if row is None:
                row = VirtualGuardState(run_id=self.run_id)
                session.add(row)
            row.state = "DEMO_LIVE"
            row.active_signal_id = ""
            row.active_shadow_duration = 0
            row.virtual_wins = 0
            row.updated_at = utc_now()

    def activate_after_demo_loss(self) -> None:
        with self.database.session() as session:
            row = session.get(VirtualGuardState, self.run_id, with_for_update=True)
            if row is None:
                row = VirtualGuardState(run_id=self.run_id)
                session.add(row)
            row.state = "WAITING_FOR_VIRTUAL_WIN"
            row.active_signal_id = ""
            row.active_shadow_duration = 0
            row.demo_losses += 1
            row.virtual_wins = 0
            row.updated_at = utc_now()

    def start_virtual_contract(self, signal_id: str, duration_ticks: int) -> bool:
        with self.database.session() as session:
            row = session.get(VirtualGuardState, self.run_id, with_for_update=True)
            if row is None or row.state != "WAITING_FOR_VIRTUAL_WIN":
                return False
            shadow = session.scalar(
                select(ShadowContract).where(
                    ShadowContract.run_id == self.run_id,
                    ShadowContract.signal_id == signal_id,
                    ShadowContract.duration_ticks == int(duration_ticks),
                    ShadowContract.status == "OPEN",
                )
            )
            if shadow is None:
                return False
            row.state = "VIRTUAL_CONTRACT_ACTIVE"
            row.active_signal_id = signal_id
            row.active_shadow_duration = int(duration_ticks)
            row.updated_at = utc_now()
            shadow.execution_state = "VIRTUAL_ACTIVE"
            shadow.execution_reason = "virtual guard evaluation"
            return True

    def apply_virtual_settlement(self, settled: dict[str, Any]) -> str | None:
        with self.database.session() as session:
            row = session.get(VirtualGuardState, self.run_id, with_for_update=True)
            if (
                row is None
                or row.state != "VIRTUAL_CONTRACT_ACTIVE"
                or row.active_signal_id != settled["signal_id"]
                or row.active_shadow_duration != int(settled["duration_ticks"])
            ):
                return None
            if settled["outcome"] == "WIN":
                row.state = "ARMED_AFTER_VIRTUAL_WIN"
                row.virtual_wins += 1
            else:
                row.state = "WAITING_FOR_VIRTUAL_WIN"
                row.virtual_wins = 0
            row.active_signal_id = ""
            row.active_shadow_duration = 0
            row.updated_at = utc_now()
            return row.state

    def consume_armed_guard(self) -> None:
        with self.database.session() as session:
            row = session.get(VirtualGuardState, self.run_id, with_for_update=True)
            if row is not None and row.state == "ARMED_AFTER_VIRTUAL_WIN":
                row.state = "DEMO_LIVE"
                row.updated_at = utc_now()

    def plan_stake(
        self,
        *,
        managed_account_id: int,
        current_balance: float,
        requested_stake: float,
        proposal_profit_ratio: float,
        recovery_enabled: bool,
        recovery_trigger_losses: int,
        minimum_stake: float,
    ) -> StakePlan:
        today = datetime.now(timezone.utc).date().isoformat()
        balance = max(0.0, float(current_balance))
        with self.database.session() as session:
            state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
            if state is None:
                state = AccountRiskState(
                    managed_account_id=int(managed_account_id),
                    trading_day=today,
                    daily_start_balance=balance,
                    session_profit=0.0,
                    consecutive_losses=0,
                    recovery_loss_debt=0.0,
                    recovery_pending=False,
                    recovery_attempt_active=False,
                    equity_high_water=balance,
                )
                session.add(state)
            elif state.trading_day != today:
                state.trading_day = today
                state.daily_start_balance = balance
                state.session_profit = 0.0
                state.consecutive_losses = 0
                state.recovery_loss_debt = 0.0
                state.recovery_pending = False
                state.recovery_attempt_active = False
                state.equity_high_water = balance

            state.equity_high_water = max(state.equity_high_water, balance)
            base_stake = (
                math.ceil(max(float(minimum_stake), float(requested_stake)) * 100.0 - 1e-9)
                / 100.0
            )
            if base_stake > balance + 1e-9:
                return StakePlan(None, "insufficient account balance for configured stake")

            is_recovery = bool(
                recovery_enabled
                and state.recovery_pending
                and not state.recovery_attempt_active
                and state.consecutive_losses >= int(recovery_trigger_losses)
                and state.recovery_loss_debt > 0
            )
            required_recovery_stake = 0.0
            target_stake = base_stake
            reason = ""
            if is_recovery:
                ratio = float(proposal_profit_ratio)
                if ratio <= 0:
                    reason = "recovery economics unavailable; continuing with configured stake"
                else:
                    required_recovery_stake = (
                        math.ceil((state.recovery_loss_debt / ratio) * 100.0 - 1e-9)
                        / 100.0
                    )
                    target_stake = max(float(minimum_stake), required_recovery_stake)
                    if target_stake > balance + 1e-9:
                        reason = (
                            "recovery target exceeds available balance; continuing with "
                            "configured stake"
                        )
                if reason:
                    is_recovery = False
                    target_stake = base_stake
                    state.consecutive_losses = 0
                    state.recovery_loss_debt = 0.0
                    state.recovery_pending = False
                    state.recovery_attempt_active = False

            state.updated_at = utc_now()
            return StakePlan(
                target_stake,
                reason,
                is_recovery=is_recovery,
                recovery_debt=state.recovery_loss_debt,
                required_recovery_stake=required_recovery_stake,
            )

    def effective_stake(
        self,
        *,
        managed_account_id: int,
        current_balance: float,
        requested_stake: float,
        minimum_stake: float,
    ) -> tuple[float | None, str]:
        """Compatibility wrapper for callers that require fixed-risk stake only."""
        plan = self.plan_stake(
            managed_account_id=managed_account_id,
            current_balance=current_balance,
            requested_stake=requested_stake,
            proposal_profit_ratio=0.0,
            recovery_enabled=False,
            recovery_trigger_losses=2,
            minimum_stake=minimum_stake,
        )
        return plan.stake, plan.reason

    def mark_recovery_attempt_started(self, managed_account_id: int) -> bool:
        with self.database.session() as session:
            state = session.get(
                AccountRiskState,
                int(managed_account_id),
                with_for_update=True,
            )
            if (
                state is None
                or not state.recovery_pending
                or state.recovery_attempt_active
            ):
                return False
            state.recovery_pending = False
            state.recovery_attempt_active = True
            state.updated_at = utc_now()
            return True

    def record_account_outcome(
        self,
        *,
        managed_account_id: int,
        profit: float,
        current_balance: float,
        recovery_enabled: bool = False,
        recovery_trigger_losses: int = 2,
    ) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date().isoformat()
        with self.database.session() as session:
            state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
            if state is None:
                state = AccountRiskState(
                    managed_account_id=int(managed_account_id),
                    trading_day=today,
                    daily_start_balance=max(0.0, float(current_balance) - float(profit)),
                    session_profit=0.0,
                    consecutive_losses=0,
                    recovery_loss_debt=0.0,
                    recovery_pending=False,
                    recovery_attempt_active=False,
                    equity_high_water=max(0.0, float(current_balance)),
                )
                session.add(state)
            state.session_profit += float(profit)
            was_recovery = bool(state.recovery_attempt_active)
            if was_recovery:
                state.consecutive_losses = (
                    0 if profit > 0 else state.consecutive_losses + 1
                )
                # One attempt means neither a recovery win nor loss can start a
                # second chase from the same debt cycle.
                state.recovery_loss_debt = 0.0
                state.recovery_pending = False
                state.recovery_attempt_active = False
            elif profit <= 0:
                state.consecutive_losses += 1
                state.recovery_loss_debt += abs(float(profit))
                state.recovery_pending = bool(
                    recovery_enabled
                    and state.consecutive_losses >= int(recovery_trigger_losses)
                )
            else:
                state.consecutive_losses = 0
                state.recovery_loss_debt = 0.0
                state.recovery_pending = False
                state.recovery_attempt_active = False
            state.equity_high_water = max(state.equity_high_water, float(current_balance))
            state.updated_at = utc_now()
            return {
                "session_profit": state.session_profit,
                "consecutive_losses": state.consecutive_losses,
                "recovery_loss_debt": state.recovery_loss_debt,
                "recovery_pending": state.recovery_pending,
                "recovery_attempt_active": state.recovery_attempt_active,
                "settled_recovery_attempt": was_recovery,
                "daily_start_balance": state.daily_start_balance,
                "equity_high_water": state.equity_high_water,
            }
