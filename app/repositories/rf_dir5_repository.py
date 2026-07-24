from __future__ import annotations

import math
import uuid
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
    VirtualTrade,
    VirtualGuardState,
    utc_now,
)
from app.strategy.rise_fall_strategy import SignalEvent, shadow_outcome

NORMAL_MODE = "NORMAL_MODE"
VIRTUAL_MODE = "VIRTUAL_MODE"
RECOVERY_PENDING = "RECOVERY_PENDING"
VIRTUAL_WAITING_FOR_WIN = "VIRTUAL_WAITING_FOR_WIN"
REAL_RECOVERY_PENDING = "REAL_RECOVERY_PENDING"
ACTUAL_TRADE = "ACTUAL_TRADE"
VIRTUAL_TRADE = "VIRTUAL_TRADE"
VIRTUAL_WIN = "VIRTUAL_WIN"
VIRTUAL_LOSS = "VIRTUAL_LOSS"


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

    @staticmethod
    def _mode_label(state: AccountRiskState | None) -> str:
        if state is None:
            return NORMAL_MODE
        if state.protection_mode == VIRTUAL_WAITING_FOR_WIN:
            return VIRTUAL_MODE
        if state.protection_mode == REAL_RECOVERY_PENDING:
            return RECOVERY_PENDING
        return NORMAL_MODE

    def _default_virtual_state(self, account_id_masked: str = "") -> dict[str, Any]:
        return {
            "mode": NORMAL_MODE,
            "state": NORMAL_MODE,
            "account": str(account_id_masked or ""),
            "consecutive_actual_losses": 0,
            "actual_recovery_debt": 0.0,
            "virtual_observations": 0,
            "virtual_wins": 0,
            "virtual_losses": 0,
            "current_virtual_loss_streak": 0,
            "entered_virtual_mode_at": None,
            "recovery_pending_since": None,
            "next_action": "Trading normally",
        }

    def _protection_payload(self, state: AccountRiskState | None) -> dict[str, Any]:
        if state is None:
            return self._default_virtual_state()
        mode = self._mode_label(state)
        if mode == VIRTUAL_MODE:
            next_action = "Waiting for a virtual win"
        elif mode == RECOVERY_PENDING:
            next_action = "Next qualifying entry will be a real recovery trade"
        else:
            next_action = "Trading normally"
        return {
            "mode": mode,
            "state": state.protection_mode,
            "account": state.account_id_masked,
            "consecutive_actual_losses": int(state.consecutive_losses or 0),
            "actual_recovery_debt": float(state.recovery_loss_debt or 0.0),
            "virtual_observations": int(state.virtual_observation_count or 0),
            "virtual_wins": int(state.virtual_win_count or 0),
            "virtual_losses": int(state.virtual_loss_count or 0),
            "current_virtual_loss_streak": int(
                state.current_virtual_loss_streak or 0
            ),
            "entered_virtual_mode_at": (
                state.entered_virtual_mode_at.isoformat()
                if state.entered_virtual_mode_at
                else None
            ),
            "recovery_pending_since": (
                state.recovery_pending_since.isoformat()
                if state.recovery_pending_since
                else None
            ),
            "next_action": next_action,
        }

    def virtual_protection_for_account(
        self,
        *,
        managed_account_id: int | None = None,
        account_id_masked: str = "",
    ) -> dict[str, Any]:
        with self.database.session() as session:
            state = None
            if managed_account_id is not None:
                state = session.get(AccountRiskState, int(managed_account_id))
            elif account_id_masked:
                state = session.scalar(
                    select(AccountRiskState).where(
                        AccountRiskState.account_id_masked == str(account_id_masked)
                    )
                )
        if state is None:
            return self._default_virtual_state(account_id_masked)
        return self._protection_payload(state)

    def virtual_totals(self) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.execute(
                select(
                    func.count().label("observations"),
                    func.sum(case((VirtualTrade.result == VIRTUAL_WIN, 1), else_=0)).label("wins"),
                    func.sum(case((VirtualTrade.result == VIRTUAL_LOSS, 1), else_=0)).label("losses"),
                ).where(VirtualTrade.run_id == self.run_id)
            ).one()
            active = session.scalar(
                select(func.count())
                .select_from(AccountRiskState)
                .where(AccountRiskState.protection_mode == VIRTUAL_WAITING_FOR_WIN)
            )
            recovery_pending = session.scalar(
                select(func.count())
                .select_from(AccountRiskState)
                .where(AccountRiskState.protection_mode == REAL_RECOVERY_PENDING)
            )
        observations = int(row.observations or 0)
        wins = int(row.wins or 0)
        losses = int(row.losses or 0)
        return {
            "observations": observations,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / (wins + losses) if wins + losses else 0.0,
            "active_accounts": int(active or 0),
            "recovery_pending_accounts": int(recovery_pending or 0),
        }

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
        account_id_masked: str = "",
        current_balance: float,
        requested_stake: float,
        proposal_profit_ratio: float,
        recovery_enabled: bool,
        recovery_trigger_losses: int,
        minimum_stake: float,
        virtual_protection_enabled: bool = True,
        maximum_recovery_balance_fraction: float = 0.10,
        minimum_balance_reserve: float = 0.50,
    ) -> StakePlan:
        today = datetime.now(timezone.utc).date().isoformat()
        balance = max(0.0, float(current_balance))
        with self.database.session() as session:
            state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
            if state is None:
                state = AccountRiskState(
                    managed_account_id=int(managed_account_id),
                    account_id_masked=str(account_id_masked or ""),
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
            elif account_id_masked and state.account_id_masked != account_id_masked:
                state.account_id_masked = str(account_id_masked)
            elif state.trading_day != today:
                state.trading_day = today
                state.daily_start_balance = balance
                state.session_profit = 0.0
                state.equity_high_water = balance

            state.equity_high_water = max(state.equity_high_water, balance)
            if (
                virtual_protection_enabled
                and state.protection_mode == VIRTUAL_WAITING_FOR_WIN
            ):
                state.updated_at = utc_now()
                return StakePlan(
                    None,
                    "virtual protection waiting for virtual win; debt retained",
                    is_recovery=bool(
                        recovery_enabled
                        and state.recovery_pending
                        and state.recovery_loss_debt > 0
                    ),
                    recovery_debt=state.recovery_loss_debt,
                )
            base_stake = (
                math.ceil(max(float(minimum_stake), float(requested_stake)) * 100.0 - 1e-9)
                / 100.0
            )
            spendable_balance = max(0.0, balance - float(minimum_balance_reserve))
            if base_stake > spendable_balance + 1e-9:
                return StakePlan(
                    None,
                    "insufficient account balance for configured stake and reserve",
                    recovery_debt=state.recovery_loss_debt,
                )

            is_recovery = bool(
                recovery_enabled
                and state.recovery_pending
                and not state.recovery_attempt_active
                and state.recovery_loss_debt > 0
            )
            required_recovery_stake = 0.0
            target_stake = base_stake
            reason = ""
            if is_recovery:
                ratio = float(proposal_profit_ratio)
                if ratio <= 0:
                    return StakePlan(
                        None,
                        "recovery economics unavailable; debt retained",
                        recovery_debt=state.recovery_loss_debt,
                    )
                else:
                    required_recovery_stake = (
                        math.ceil((state.recovery_loss_debt / ratio) * 100.0 - 1e-9)
                        / 100.0
                    )
                    target_stake = max(base_stake, required_recovery_stake)
                    recovery_safety_cap = min(
                        spendable_balance,
                        max(
                            base_stake,
                            balance * float(maximum_recovery_balance_fraction),
                        ),
                    )
                    if target_stake > recovery_safety_cap + 1e-9:
                        return StakePlan(
                            None,
                            "recovery stake exceeds account balance safety cap; debt retained",
                            is_recovery=True,
                            recovery_debt=state.recovery_loss_debt,
                            required_recovery_stake=required_recovery_stake,
                        )

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
        account_id_masked: str = "",
        profit: float,
        current_balance: float,
        recovery_enabled: bool = False,
        recovery_trigger_losses: int = 1,
        virtual_protection_enabled: bool = True,
        virtual_trigger_actual_losses: int = 2,
    ) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date().isoformat()
        with self.database.session() as session:
            state = session.get(AccountRiskState, int(managed_account_id), with_for_update=True)
            if state is None:
                state = AccountRiskState(
                    managed_account_id=int(managed_account_id),
                    account_id_masked=str(account_id_masked or ""),
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
            elif account_id_masked and state.account_id_masked != account_id_masked:
                state.account_id_masked = str(account_id_masked)
            state.session_profit += float(profit)
            was_recovery = bool(state.recovery_attempt_active)
            state.recovery_attempt_active = False
            previous_mode = state.protection_mode
            if profit <= 0:
                state.consecutive_losses += 1
                state.recovery_loss_debt = round(
                    state.recovery_loss_debt + abs(float(profit)),
                    2,
                )
                state.recovery_pending = bool(
                    recovery_enabled
                    and state.consecutive_losses >= int(recovery_trigger_losses)
                )
                if (
                    virtual_protection_enabled
                    and state.consecutive_losses >= int(virtual_trigger_actual_losses)
                ):
                    if state.protection_mode != VIRTUAL_WAITING_FOR_WIN:
                        state.entered_virtual_mode_at = utc_now()
                    state.protection_mode = VIRTUAL_WAITING_FOR_WIN
            else:
                state.recovery_loss_debt = max(
                    0.0,
                    round(state.recovery_loss_debt - float(profit), 2),
                )
                state.recovery_pending = bool(
                    recovery_enabled and state.recovery_loss_debt >= 0.01
                )
                if not state.recovery_pending:
                    state.consecutive_losses = 0
                    state.recovery_pending_since = None
                state.protection_mode = NORMAL_MODE
                state.entered_virtual_mode_at = None
            if state.recovery_pending and state.recovery_pending_since is None:
                state.recovery_pending_since = utc_now()
            if not state.recovery_pending:
                state.recovery_pending_since = None
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
                "protection_mode": self._mode_label(state),
                "raw_protection_state": state.protection_mode,
                "protection_state_changed": previous_mode != state.protection_mode,
            }

    def start_virtual_trade(
        self,
        *,
        managed_account_id: int,
        account_id_masked: str,
        signal: SignalEvent,
        configured_stake: float,
        simulated_stake: float,
        expected_payout: float | None,
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.session() as session:
            state = session.get(
                AccountRiskState,
                int(managed_account_id),
                with_for_update=True,
            )
            if state is None or state.protection_mode != VIRTUAL_WAITING_FOR_WIN:
                return None
            existing = session.scalar(
                select(VirtualTrade).where(
                    VirtualTrade.managed_account_id == int(managed_account_id),
                    VirtualTrade.signal_id == signal.signal_id,
                )
            )
            if existing is not None:
                return None
            active = session.scalar(
                select(VirtualTrade.id).where(
                    VirtualTrade.managed_account_id == int(managed_account_id),
                    VirtualTrade.result == "OPEN",
                )
            )
            if active is not None:
                return None
            trade = VirtualTrade(
                virtual_trade_id=f"virtual-{uuid.uuid4()}",
                managed_account_id=int(managed_account_id),
                account_id_masked=str(account_id_masked),
                run_id=self.run_id,
                signal_id=signal.signal_id,
                execution_session_id=signal.connection_session_id,
                strategy_id=signal.strategy_version,
                market=signal.symbol,
                direction=signal.direction,
                contract_type=signal.contract_type,
                barrier=str(signal.barrier or ""),
                prediction_digit=None,
                duration=int(signal.duration_ticks),
                duration_unit="t",
                signal_time=now,
                entry_tick_sequence=int(signal.tick_sequence),
                exit_tick_sequence=int(signal.tick_sequence) + int(signal.duration_ticks),
                entry_tick_epoch=int(signal.signal_tick_epoch or 0),
                entry_spot=float(signal.reference_entry_quote),
                configured_stake=float(configured_stake),
                simulated_stake=float(simulated_stake),
                expected_payout=expected_payout,
                result="OPEN",
                reason="VIRTUAL_MODE",
                amount_charged=0.0,
                actual_profit_loss=0.0,
                actual_payout=0.0,
                recovery_debt_change=0.0,
                created_at=now,
            )
            session.add(trade)
            state.updated_at = now
            return {
                "virtual_trade_id": trade.virtual_trade_id,
                "account": trade.account_id_masked,
                "market": trade.market,
                "simulated_stake": trade.simulated_stake,
                "recovery_debt": float(state.recovery_loss_debt or 0.0),
            }

    def settle_due_virtual_trades(
        self,
        *,
        symbol: str,
        tick_sequence: int,
        exit_quote: Decimal,
        exit_epoch: int = 0,
        exit_after_wins: int = 2,
        max_observations: int = 0,
    ) -> list[dict[str, Any]]:
        settled: list[dict[str, Any]] = []
        now = utc_now()
        required_virtual_wins = max(1, int(exit_after_wins or 1))
        observation_cap = max(0, int(max_observations or 0))
        with self.database.session() as session:
            rows = session.scalars(
                select(VirtualTrade)
                .where(
                    VirtualTrade.run_id == self.run_id,
                    VirtualTrade.market == str(symbol),
                    VirtualTrade.result == "OPEN",
                    VirtualTrade.exit_tick_sequence <= int(tick_sequence),
                )
                .with_for_update()
            ).all()
            for trade in rows:
                state = session.get(
                    AccountRiskState,
                    int(trade.managed_account_id),
                    with_for_update=True,
                )
                if state is None or state.protection_mode != VIRTUAL_WAITING_FOR_WIN:
                    trade.result = "VIRTUAL_STALE"
                    trade.reason = "Virtual observation ignored after mode changed"
                    trade.amount_charged = 0.0
                    trade.actual_profit_loss = 0.0
                    trade.actual_payout = 0.0
                    trade.recovery_debt_change = 0.0
                    trade.settled_at = now
                    continue
                outcome = shadow_outcome(
                    trade.direction,
                    Decimal(str(trade.entry_spot)),
                    Decimal(str(exit_quote)),
                )
                result = VIRTUAL_WIN if outcome == "WIN" else VIRTUAL_LOSS
                trade.exit_spot = float(exit_quote)
                trade.exit_tick_epoch = int(exit_epoch or 0)
                try:
                    trade.actual_last_digit = int(str(exit_quote).replace(".", "")[-1])
                except (TypeError, ValueError, IndexError):
                    trade.actual_last_digit = None
                trade.result = result
                trade.reason = "Hypothetical Outcome - No Purchase"
                trade.amount_charged = 0.0
                trade.actual_profit_loss = 0.0
                trade.actual_payout = 0.0
                trade.recovery_debt_change = 0.0
                trade.settled_at = now
                state.virtual_observation_count += 1
                observations_query = (
                    select(func.count())
                    .select_from(VirtualTrade)
                    .where(
                        VirtualTrade.managed_account_id
                        == int(trade.managed_account_id),
                        VirtualTrade.result.in_((VIRTUAL_WIN, VIRTUAL_LOSS)),
                    )
                )
                if state.entered_virtual_mode_at is not None:
                    observations_query = observations_query.where(
                        VirtualTrade.created_at >= state.entered_virtual_mode_at
                    )
                observations_in_mode = int(session.scalar(observations_query) or 0)
                consecutive_virtual_wins = (
                    int(state.virtual_win_count or 0) + 1
                    if result == VIRTUAL_WIN
                    else 0
                )
                exit_virtual_mode = bool(
                    (
                        result == VIRTUAL_WIN
                        and consecutive_virtual_wins >= required_virtual_wins
                    )
                    or (
                        observation_cap > 0
                        and observations_in_mode >= observation_cap
                    )
                )
                if result == VIRTUAL_WIN:
                    state.virtual_win_count = consecutive_virtual_wins
                    state.current_virtual_loss_streak = 0
                else:
                    state.virtual_win_count = 0
                    state.virtual_loss_count += 1
                    state.current_virtual_loss_streak += 1
                if exit_virtual_mode:
                    state.protection_mode = REAL_RECOVERY_PENDING
                    state.recovery_pending = bool(state.recovery_loss_debt >= 0.01)
                    if state.recovery_pending_since is None:
                        state.recovery_pending_since = now
                else:
                    state.protection_mode = VIRTUAL_WAITING_FOR_WIN
                state.updated_at = now
                payload = self._protection_payload(state)
                settled.append(
                    {
                        "virtual_trade_id": trade.virtual_trade_id,
                        "account": trade.account_id_masked,
                        "market": trade.market,
                        "result": result,
                        "entry_spot": trade.entry_spot,
                        "exit_spot": trade.exit_spot,
                        "actual_financial_impact": 0.0,
                        "protection": payload,
                    }
                )
        return settled
