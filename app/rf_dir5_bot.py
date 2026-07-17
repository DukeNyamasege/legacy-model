from __future__ import annotations

import asyncio
import json
import math
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.model.bayesian_probability import BayesianGroupKey, KeyedBayesianProbability
from app.repositories.rf_dir5_repository import RFDir5Repository
from app.strategy.decision_engine import (
    ProposalEconomics,
    RiseFallDecisionEngine,
    parse_proposal_economics,
)
from app.strategy.rise_fall_strategy import (
    RF_DIR5_VERSION,
    SignalEvent,
    build_five_move_features,
    calculate_directional_score,
    check_exhaustion_filter,
    check_volatility_filter,
    detect_fall_candidate,
    detect_rise_candidate,
    make_signal_event,
)
from enhanced_bot import TradingBot, mask_account_id, optional_float, optional_epoch_datetime


class _ExecutionDisabledBayesian:
    def update(self, _won: bool) -> None:
        return


class RFDir5TradingBot(TradingBot):
    """RF-DIR5 production worker using the proven account/transport envelope."""

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__(config_path)
        self.rf_config = self.test2_config.rf_strategy
        self.virtual_guard_config = self.test2_config.virtual_guard
        self.risk_config = self.test2_config.risk
        self.symbols = list(self.rf_config.markets)
        self.symbol = self.symbols[0]
        self.duration = self.rf_config.demo_duration_ticks
        self.duration_unit = "t"
        self.contract_type = "CALL"
        self.contract_barrier = ""
        self.pattern_length = self.rf_config.analysis_movements
        self.max_open_trade_seconds = max(self.max_open_trade_seconds, 30)

        history_size = self.rf_config.minimum_history_movements + 16
        for market in self.market_states.values():
            market.ticks_history = deque(maxlen=history_size)
            market.live_ticks_history = deque(maxlen=6)
            market.raw_tick_digits.clear()
        primary = self.market_states[self.symbol]
        self.ticks_history = primary.ticks_history
        self.live_ticks_history = primary.live_ticks_history
        self.raw_tick_digits = primary.raw_tick_digits

        self.rf_repository = RFDir5Repository(self.repository)
        self.keyed_bayesian = KeyedBayesianProbability(
            prior_alpha=self.test2_config.bayesian.prior_alpha,
            prior_beta=self.test2_config.bayesian.prior_beta,
            credible_interval=self.test2_config.bayesian.credible_interval,
            minimum_completed_trades=self.test2_config.bayesian.minimum_shadow_outcomes,
        )
        self.rf_decision_engine = RiseFallDecisionEngine(
            minimum_score=self.rf_config.minimum_directional_score,
            stale_signal_after_ms=self.rf_config.stale_signal_after_ms,
            minimum_shadow_outcomes=self.test2_config.bayesian.minimum_shadow_outcomes,
            required_edge_margin=self.test2_config.bayesian.required_edge_margin,
            real_gate_enabled=self.test2_config.bayesian.real_gate_enabled,
        )
        # The legacy global posterior remains import-compatible but is never updated or
        # consulted by RF-DIR5 execution.
        self.bayesian = _ExecutionDisabledBayesian()
        self.rf_candidate_queue: list[SignalEvent] = []
        self.rf_arbitration_task: asyncio.Task | None = None
        self.rf_supported_contracts: dict[str, set[str]] = {}
        self.rf_contract_validation_task: asyncio.Task | None = None
        self.rf_account_contract_tasks: dict[str, asyncio.Task] = {}
        self.rf_account_supported_contracts: dict[tuple[str, str], set[str]] = {}
        self.rf_last_epoch: dict[str, int] = {}
        self.rf_last_tick_id: dict[str, str] = {}

        for state in self.clients.values():
            self._clear_recovery_state(state)
        self._save_state()
        self.logger.info(
            "RF_DIR5_ACTIVE version=%s markets=%s demo_duration=%s shadow_durations=%s "
            "martingale=disabled virtual_guard=%s",
            RF_DIR5_VERSION,
            ",".join(self.symbols),
            self.duration,
            list(self.rf_config.shadow_duration_ticks),
            self.virtual_guard_config.enabled,
        )

    @staticmethod
    def _clear_recovery_state(state: dict[str, Any]) -> None:
        state["recovery_loss_pool"] = 0.0
        state["recovery_wins_remaining"] = 1
        state["oscar_debt"] = 0.0
        state["oscar_win_streak"] = 0
        state["single_recovery_pending"] = False
        state["single_recovery_active"] = False
        state["current_stake"] = float(state.get("base_stake", 0.50))

    def _reset_session_runtime_state(self) -> None:
        super()._reset_session_runtime_state()
        if self.rf_arbitration_task and not self.rf_arbitration_task.done():
            self.rf_arbitration_task.cancel()
        self.rf_arbitration_task = None
        if self.rf_contract_validation_task and not self.rf_contract_validation_task.done():
            self.rf_contract_validation_task.cancel()
        self.rf_contract_validation_task = None
        for task in self.rf_account_contract_tasks.values():
            if not task.done():
                task.cancel()
        self.rf_account_contract_tasks.clear()
        self.rf_candidate_queue.clear()
        self.rf_supported_contracts.clear()
        self.rf_last_epoch.clear()
        self.rf_last_tick_id.clear()
        self.rf_account_supported_contracts.clear()

    def _on_public_connection_established(self) -> None:
        super()._on_public_connection_established()
        self.rf_supported_contracts.clear()
        self.rf_last_epoch.clear()
        self.rf_last_tick_id.clear()

    def _on_market_subscriptions_ready(self) -> None:
        if self.rf_contract_validation_task and not self.rf_contract_validation_task.done():
            self.rf_contract_validation_task.cancel()
        self.rf_contract_validation_task = asyncio.create_task(
            self._validate_rf_contracts(),
            name="rf_contract_validation",
        )
        self.rf_contract_validation_task.add_done_callback(
            self._public_contract_validation_finished
        )

    def _public_contract_validation_finished(self, task: asyncio.Task) -> None:
        if self.rf_contract_validation_task is task:
            self.rf_contract_validation_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.logger.error("RF_CONTRACTS_VALIDATION_FAILED error=%s", exc)

    async def _validate_rf_contracts(self) -> None:
        for symbol in self.symbols:
            response = await self.public_client.send_request(
                {"contracts_for": symbol}
            )
            if "error" in response:
                self.logger.error(
                    "RF_CONTRACTS_FOR_FAILED symbol=%s error=%s",
                    symbol,
                    response["error"].get("message", "unknown"),
                )
                continue
            payload = response.get("contracts_for") or response.get("data") or {}
            available = payload.get("available") if isinstance(payload, dict) else []
            types = {
                str(item.get("contract_type") or "").upper()
                for item in (available or [])
                if isinstance(item, dict)
            }
            self.rf_supported_contracts[symbol] = types
            if not {"CALL", "PUT"}.issubset(types):
                self.logger.error(
                    "RF_MARKET_DISABLED symbol=%s required=CALL,PUT available=%s",
                    symbol,
                    sorted(types),
                )
            else:
                self.logger.info("RF_MARKET_VERIFIED symbol=%s contracts=CALL,PUT", symbol)

    def _on_private_session_ready(self, session: Any) -> None:
        existing = self.rf_account_contract_tasks.get(session.account_id)
        if existing and not existing.done():
            existing.cancel()
        task = asyncio.create_task(
            self._validate_account_contracts(session),
            name=f"rf_account_contracts_{session.account_id}",
        )
        self.rf_account_contract_tasks[session.account_id] = task
        task.add_done_callback(
            lambda completed, account_id=session.account_id: self._account_contract_validation_finished(
                account_id,
                completed,
            )
        )

    def _account_contract_validation_finished(
        self,
        account_id: str,
        task: asyncio.Task,
    ) -> None:
        if self.rf_account_contract_tasks.get(account_id) is task:
            self.rf_account_contract_tasks.pop(account_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.logger.error(
                "RF_ACCOUNT_CONTRACTS_VALIDATION_FAILED account=%s error=%s",
                mask_account_id(account_id),
                exc,
            )

    async def _validate_account_contracts(self, session: Any) -> None:
        for symbol in self.symbols:
            response = await session.send_request({"contracts_for": symbol})
            if "error" in response:
                self.logger.error(
                    "RF_ACCOUNT_CONTRACTS_FAILED account=%s symbol=%s error=%s",
                    mask_account_id(session.account_id),
                    symbol,
                    response["error"].get("message", "unknown"),
                )
                continue
            payload = response.get("contracts_for") or {}
            available = payload.get("available") if isinstance(payload, dict) else []
            types = {
                str(item.get("contract_type") or "").upper()
                for item in (available or [])
                if isinstance(item, dict)
            }
            self.rf_account_supported_contracts[(session.account_id, symbol)] = types
        self.logger.info(
            "RF_ACCOUNT_CONTRACTS_VERIFIED account=%s markets=%s",
            mask_account_id(session.account_id),
            sum(
                {"CALL", "PUT"}.issubset(types)
                for (account_id, _symbol), types in self.rf_account_supported_contracts.items()
                if account_id == session.account_id
            ),
        )

    def _render_live_ticks(self, note: str = "") -> None:
        handler = self._get_live_console_handler()
        market = self.market_states.get(self.live_market_symbol, self.market_states[self.symbol])
        if handler is None or not market.live_ticks_history:
            return
        quotes = [Decimal(str(item["quote"])) for item in market.live_ticks_history]
        moves = [later - earlier for earlier, later in zip(quotes[:-1], quotes[1:])]
        move_text = " | ".join(f"{value:+f}" for value in moves)
        quote_text = " | ".join(str(item.get("display", item["quote"])) for item in market.live_ticks_history)
        handler.set_status(
            f"LIVE {market.symbol} | quotes=[{quote_text}] | moves=[{move_text}] | "
            f"strategy={note or 'RF-DIR5-WATCHING'}"
        )

    async def _on_tick(self, tick_data: dict[str, Any]) -> None:
        tick = tick_data.get("tick") or {}
        symbol = str(tick.get("symbol") or self.symbol)
        market = self.market_states.get(symbol)
        if market is None:
            return
        epoch = int(tick.get("epoch") or 0)
        tick_id = str(tick.get("id") or f"{symbol}:{epoch}:{tick.get('quote')}")
        if (
            epoch <= self.rf_last_epoch.get(symbol, -1)
            or tick_id == self.rf_last_tick_id.get(symbol)
        ):
            self.logger.warning(
                "RF_TICK_REJECTED symbol=%s reason=missing_duplicate_or_out_of_order epoch=%s",
                symbol,
                epoch,
            )
            return
        self.rf_last_epoch[symbol] = epoch
        self.rf_last_tick_id[symbol] = tick_id

        quote = Decimal(str(tick["quote"]))
        display_value = f"{quote:.{market.pip_size}f}"
        self.live_market_symbol = symbol
        self._mark_tick_received(market)
        self.tick_sequence += 1
        market.tick_sequence += 1
        snapshot = {
            "quote": quote,
            "display": display_value,
            "epoch": epoch,
            "tick_id": tick_id,
            "last_digit": "-",
        }
        market.live_ticks_history.append(snapshot)
        market.ticks_history.append(snapshot)
        self.repository.record_tick(
            sequence_id=self.tick_sequence,
            symbol=symbol,
            epoch=epoch,
            tick_id=tick_id,
            quote=float(quote),
            final_digit=-1,
            connection_session_id=self.connection_session_id,
        )
        self._render_live_ticks()

        settled_shadows = self.rf_repository.settle_due_shadows(
            symbol=symbol,
            tick_sequence=market.tick_sequence,
            expiry_quote=quote,
        )
        for settled in settled_shadows:
            key = BayesianGroupKey(
                settled["strategy_version"],
                settled["symbol"],
                settled["direction"],
                settled["duration_ticks"],
            )
            self.keyed_bayesian.update(key, settled["outcome"] == "WIN")
            guard_transition = self.rf_repository.apply_virtual_settlement(settled)
            self.logger.info(
                "RF_SHADOW_SETTLED signal_id=%s symbol=%s direction=%s duration=%s "
                "outcome=%s state=%s",
                settled["signal_id"],
                settled["symbol"],
                settled["direction"],
                settled["duration_ticks"],
                settled["outcome"],
                settled["execution_state"],
            )
            if guard_transition:
                self.logger.warning("RF_VIRTUAL_GUARD_TRANSITION state=%s", guard_transition)

        if not {"CALL", "PUT"}.issubset(self.rf_supported_contracts.get(symbol, set())):
            return
        if len(market.ticks_history) < self.rf_config.minimum_history_movements + 1:
            return

        quotes = [Decimal(str(item["quote"])) for item in market.ticks_history]
        historical_quotes = quotes[:-5]
        normalization = [
            later - earlier
            for earlier, later in zip(historical_quotes[:-1], historical_quotes[1:])
        ][-self.rf_config.normalization_movements :]
        try:
            features = build_five_move_features(
                quotes[-6:],
                normalization_movements=normalization,
            )
        except ValueError:
            return

        rise = detect_rise_candidate(
            features,
            minimum_directional_moves=self.rf_config.minimum_directional_moves,
            minimum_efficiency=self.rf_config.minimum_efficiency,
        )
        fall = detect_fall_candidate(
            features,
            minimum_directional_moves=self.rf_config.minimum_directional_moves,
            minimum_efficiency=self.rf_config.minimum_efficiency,
        )
        if not rise and not fall:
            return
        volatility_ok = check_volatility_filter(
            features,
            minimum_impulse=self.rf_config.minimum_impulse,
            maximum_impulse=self.rf_config.maximum_impulse,
        )
        exhaustion_ok = check_exhaustion_filter(
            features,
            maximum_move_ratio=self.rf_config.maximum_move_ratio,
        )
        if not volatility_ok or not exhaustion_ok:
            self.logger.info(
                "RF_SIGNAL_FILTERED symbol=%s impulse=%.3f largest_move_ratio=%.3f",
                symbol,
                features.impulse,
                features.largest_move_ratio,
            )
            return

        direction = "RISE" if rise else "FALL"
        quality_score = calculate_directional_score(
            features,
            direction=direction,
            volatility_ok=volatility_ok,
            exhaustion_ok=exhaustion_ok,
        )
        signal = make_signal_event(
            run_id=self.test2_config.model.run_id,
            symbol=symbol,
            direction=direction,
            duration_ticks=self.rf_config.demo_duration_ticks,
            features=features,
            quality_score=quality_score,
            signal_tick_epoch=epoch,
            signal_tick_id=tick_id,
            connection_session_id=self.connection_session_id,
            tick_sequence=market.tick_sequence,
        )
        self.rf_repository.record_signal(signal)
        self.rf_repository.create_shadow_contracts(
            signal,
            tuple(self.rf_config.shadow_duration_ticks),
        )
        self.rf_candidate_queue.append(signal)
        self.logger.info(
            "RF_SIGNAL_QUALIFIED signal_id=%s symbol=%s direction=%s score=%s "
            "efficiency=%.3f impulse=%.3f",
            signal.signal_id,
            signal.symbol,
            signal.direction,
            signal.quality_score,
            signal.features.efficiency,
            signal.features.impulse,
        )
        self._schedule_candidate_arbitration()

    def _schedule_candidate_arbitration(self) -> None:
        if self.rf_arbitration_task is not None and not self.rf_arbitration_task.done():
            return
        task = asyncio.create_task(
            self._arbitrate_candidates(),
            name="rf_candidate_arbitration",
        )
        self.rf_arbitration_task = task
        task.add_done_callback(self._candidate_arbitration_finished)

    def _candidate_arbitration_finished(self, task: asyncio.Task) -> None:
        if self.rf_arbitration_task is task:
            self.rf_arbitration_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.logger.error("RF_ARBITRATION_FAILED error=%s", exc)
        if self.rf_candidate_queue and self.is_running:
            self._schedule_candidate_arbitration()

    async def _proposal_for_duration(
        self,
        signal: SignalEvent,
        duration_ticks: int,
    ) -> tuple[ProposalEconomics | None, float, float]:
        requested = time.monotonic()
        response = await self.public_client.send_request(
            self._proposal_request_for(signal, self.base_stake, duration_ticks)
        )
        received = time.monotonic()
        if "error" in response:
            return None, requested, received
        try:
            economics = parse_proposal_economics(
                response,
                stake=self.base_stake,
                predicted_probability=0.5,
                requested_monotonic=requested,
                received_monotonic=received,
                commission_in_ask=True,
            )
        except (TypeError, ValueError):
            return None, requested, received
        self.rf_repository.update_shadow_proposal(
            signal.signal_id,
            duration_ticks,
            ask_price=economics.stake,
            payout=economics.payout,
            break_even_probability=economics.break_even_probability,
        )
        return economics, requested, received

    async def _arbitrate_candidates(self) -> None:
        await asyncio.sleep(self.rf_config.candidate_window_ms / 1000.0)
        candidates = self.rf_candidate_queue
        self.rf_candidate_queue = []
        if not candidates:
            return

        evaluated: list[tuple[SignalEvent, ProposalEconomics, float, bool]] = []
        for signal in candidates:
            duration_results = await asyncio.gather(
                *(
                    self._proposal_for_duration(signal, duration)
                    for duration in self.rf_config.shadow_duration_ticks
                )
            )
            demo_index = tuple(self.rf_config.shadow_duration_ticks).index(
                self.rf_config.demo_duration_ticks
            )
            economics, _requested, received = duration_results[demo_index]
            if economics is None:
                self._mark_rf_decision(signal, "SKIP_UNPROFITABLE_QUOTE", "invalid proposal")
                continue
            key = BayesianGroupKey(
                RF_DIR5_VERSION,
                signal.symbol,
                signal.direction,
                signal.duration_ticks,
            )
            wins, losses = self.rf_repository.shadow_group_counts(key)
            self.keyed_bayesian.restore(key, wins=wins, losses=losses)
            posterior = self.keyed_bayesian.snapshot(
                key,
                break_even_probability=economics.break_even_probability,
                safety_margin=self.test2_config.bayesian.required_edge_margin,
            )
            validated_edge = posterior.lower_credible_bound - economics.break_even_probability
            validated = posterior.ready and validated_edge > self.test2_config.bayesian.required_edge_margin
            signal.validated_edge = validated_edge if validated else None
            signal.quality_score = calculate_directional_score(
                signal.features,
                direction=signal.direction,
                volatility_ok=True,
                exhaustion_ok=True,
                validated_edge=validated,
            )
            signal.proposal_ask_price = economics.stake
            signal.proposal_payout = economics.payout
            signal.break_even_probability = economics.break_even_probability
            evaluated.append((signal, economics, received, validated))

        if not evaluated:
            return
        evaluated.sort(
            key=lambda item: (
                -(item[0].validated_edge if item[0].validated_edge is not None else -math.inf),
                -item[0].quality_score,
                -item[0].features.efficiency,
                item[1].stake,
                item[0].symbol,
            )
        )
        selected, economics, proposal_received, _validated = evaluated[0]
        for signal, _economics, _received, _is_validated in evaluated[1:]:
            self._mark_rf_decision(signal, "SHADOW_ONLY", "candidate ranking", selected=False)

        market = self.market_states[selected.symbol]
        signal_age_ms = (time.monotonic() - selected.generated_monotonic) * 1000.0
        proposal_age_ms = (time.monotonic() - proposal_received) * 1000.0
        if market.tick_sequence != selected.tick_sequence:
            self._mark_rf_decision(selected, "SKIP_STALE_SIGNAL", "new tick before purchase", selected=True)
            return

        guard = self.rf_repository.guard_state()
        if guard["state"] == "WAITING_FOR_VIRTUAL_WIN":
            started = self.rf_repository.start_virtual_contract(
                selected.signal_id,
                self.rf_config.demo_duration_ticks,
            )
            self._mark_rf_decision(
                selected,
                "SKIP_VIRTUAL_GUARD",
                "virtual contract active" if started else "virtual guard unavailable",
                selected=True,
            )
            return
        if guard["state"] == "VIRTUAL_CONTRACT_ACTIVE":
            self._mark_rf_decision(selected, "SKIP_VIRTUAL_GUARD", guard["state"], selected=True)
            return

        status, _pause_reason = self.repository.control_state()
        execution_mode = self.environment
        decision = self.rf_decision_engine.decide(
            quality_score=selected.quality_score,
            signal_age_ms=signal_age_ms,
            proposal_age_ms=proposal_age_ms,
            proposal_economics=economics,
            shadow_snapshot=self.keyed_bayesian.snapshot(
                BayesianGroupKey(RF_DIR5_VERSION, selected.symbol, selected.direction, selected.duration_ticks),
                break_even_probability=economics.break_even_probability,
                safety_margin=self.test2_config.bayesian.required_edge_margin,
            ),
            execution_mode=execution_mode,
            virtual_guard_state=guard["state"],
            trading_locked=(
                self.is_trading_locked
                or bool(self.pending_contracts_for_current_cycle)
                or status in {"STOPPED", "MANUAL_PAUSE"}
            ),
        )
        if decision.action != "BUY_DEMO" or not self.test2_config.execution.demo_enabled:
            self._mark_rf_decision(selected, decision.action, ",".join(decision.reasons), selected=True)
            return
        if market.tick_sequence != selected.tick_sequence:
            self._mark_rf_decision(selected, "SKIP_STALE_SIGNAL", "tick changed after decision", selected=True)
            return
        await self._buy_selected_demo(selected, economics, guard["state"])

    def _mark_rf_decision(
        self,
        signal: SignalEvent,
        action: str,
        reason: str,
        *,
        selected: bool = False,
    ) -> None:
        self.rf_repository.set_signal_decision(
            signal.signal_id,
            action,
            reason,
            selected=selected,
            validated_edge=signal.validated_edge,
        )
        self.repository.mark_signal(
            signal.signal_id,
            status=action,
            stale=action == "SKIP_STALE_SIGNAL",
        )
        self.logger.info(
            "RF_DECISION signal_id=%s symbol=%s direction=%s action=%s reason=%s",
            signal.signal_id,
            signal.symbol,
            signal.direction,
            action,
            reason,
        )

    async def _buy_selected_demo(
        self,
        signal: SignalEvent,
        economics: ProposalEconomics,
        guard_state: str,
    ) -> None:
        eligible = self._eligible_purchase_accounts()
        stake_by_token: dict[str, float] = {}
        filtered: list[tuple[str, str]] = []
        for token, account_id in eligible:
            if signal.contract_type not in self.rf_account_supported_contracts.get(
                (account_id, signal.symbol),
                set(),
            ):
                self.logger.warning(
                    "RF_ACCOUNT_SKIPPED account=%s reason=contract_not_verified symbol=%s type=%s",
                    mask_account_id(account_id),
                    signal.symbol,
                    signal.contract_type,
                )
                continue
            state = self._client_state_for_token(token, account_id=account_id)
            managed_id = self._managed_account_id_for_token(token)
            summary = self.repository.account_summary(account_id)
            if managed_id is None:
                continue
            stake, reason = self.rf_repository.effective_stake(
                managed_account_id=managed_id,
                current_balance=float(summary.get("balance") or 0.0),
                requested_stake=float(state.get("base_stake", self.base_stake)),
                maximum_balance_percent=self.risk_config.maximum_stake_balance_percent,
                daily_drawdown_percent=self.risk_config.daily_drawdown_percent,
                maximum_equity_drawdown_percent=self.risk_config.maximum_equity_drawdown_percent,
                minimum_stake=self.base_stake,
            )
            if stake is None:
                self.logger.warning(
                    "RF_ACCOUNT_SKIPPED account=%s reason=%s",
                    mask_account_id(account_id),
                    reason,
                )
                continue
            filtered.append((token, account_id))
            stake_by_token[token] = stake
        if not filtered:
            self._mark_rf_decision(signal, "SKIP_INSUFFICIENT_BALANCE", "no risk-eligible accounts", selected=True)
            return

        self.is_trading_locked = True
        self.pending_signal = signal
        try:
            if self.market_states[signal.symbol].tick_sequence != signal.tick_sequence:
                self._mark_rf_decision(signal, "SKIP_STALE_SIGNAL", "tick changed before transport", selected=True)
                return
            self.repository.consume_signal(signal.signal_id)
            signal.consumed = True
            self.repository.mark_signal(signal.signal_id, status="PURCHASE_REQUESTED", purchase_requested=True)
            transactions = await self._purchase_accounts_by_stake(
                signal=signal,
                eligible_accounts=filtered,
                stake_by_token=stake_by_token,
            )
            contracts: set[int] = set()
            registered: set[str] = set()
            master_id = self._copytrading_master_account_id()
            self.outcomes_by_signal[signal.signal_id] = {}
            self.signal_master_account_ids[signal.signal_id] = master_id
            self.signal_symbols[signal.signal_id] = signal.symbol
            requested_at = datetime.now(timezone.utc)
            for transaction in transactions:
                account_id = str(transaction.get("account_id") or "")
                token = next((value for value, account in filtered if account == account_id), None)
                if token is None or "error" in transaction:
                    continue
                stake = float(transaction.get("stake_amount", stake_by_token[token]))
                contract_id = await self._register_account_purchase(
                    signal=signal,
                    transaction=transaction,
                    token=token,
                    account_id=account_id,
                    stake_amount=stake,
                    profit_ratio=economics.potential_profit / economics.stake,
                    purchase_requested_at=requested_at,
                )
                if contract_id is not None:
                    contracts.add(contract_id)
                    registered.add(account_id)
            self.pending_by_signal[signal.signal_id] = contracts
            if not contracts:
                self._mark_rf_decision(signal, "SHADOW_ONLY", "demo purchase failed", selected=True)
                return
            self.repository.mark_signal(signal.signal_id, status="PURCHASE_CONFIRMED", purchase_confirmed=True)
            self.rf_repository.set_signal_decision(
                signal.signal_id,
                "BUY_DEMO",
                "EXPLORATION",
                selected=True,
                validated_edge=signal.validated_edge,
            )
            if guard_state == "ARMED_AFTER_VIRTUAL_WIN":
                self.rf_repository.consume_armed_guard()
            asyncio.create_task(self._cycle_timeout_watchdog(signal.signal_id, list(contracts)))
        finally:
            self.is_trading_locked = False
            self.pending_signal = None

    def _proposal_request_for(
        self,
        signal: SignalEvent,
        stake_amount: float,
        duration_ticks: int,
    ) -> dict[str, Any]:
        return self._contract_parameters_for(signal, stake_amount, duration_ticks) | {"proposal": 1}

    def _contract_parameters_for(
        self,
        signal: SignalEvent,
        stake_amount: float,
        duration_ticks: int,
    ) -> dict[str, Any]:
        return {
            "amount": round(float(stake_amount), 2),
            "basis": "stake",
            "contract_type": signal.contract_type,
            "currency": self.currency,
            "duration": int(duration_ticks),
            "duration_unit": "t",
            "underlying_symbol": signal.symbol,
        }

    def _contract_parameters(
        self,
        signal: SignalEvent,
        stake_amount: float,
        *,
        symbol_key: str,
    ) -> dict[str, Any]:
        values = self._contract_parameters_for(signal, stake_amount, signal.duration_ticks)
        if symbol_key != "underlying_symbol":
            values[symbol_key] = values.pop("underlying_symbol")
        return values

    def _direct_buy_request(self, signal: SignalEvent, stake_amount: float) -> dict[str, Any]:
        request = {
            "buy": "1",
            "price": round(float(stake_amount), 2),
            "parameters": self._contract_parameters_for(
                signal,
                stake_amount,
                signal.duration_ticks,
            ),
        }
        if self.app_markup_percentage > 0:
            request["parameters"]["app_markup_percentage"] = round(
                self.app_markup_percentage,
                4,
            )
        return request

    def _eligible_purchase_accounts(self) -> list[tuple[str, str]]:
        return [
            (token, account_id)
            for token, account_id in super()._eligible_purchase_accounts()
            if not self.sessions[token].pending_contracts
        ]

    def _planned_stake_for_account(
        self,
        token: str,
        account_id: str,
        profit_ratio: float,
    ) -> float:
        del profit_ratio
        state = self._client_state_for_token(token, account_id=account_id)
        return round(float(state.get("base_stake", self.base_stake)), 2)

    def _update_client_recovery_state(
        self,
        state: dict[str, Any],
        *,
        outcome: str,
        profit: float,
    ) -> None:
        self._clear_recovery_state(state)
        state["loss_streak"] = int(state.get("loss_streak", 0)) + 1 if outcome != "win" else 0
        managed_id = state.get("managed_account_id")
        account_id = str(state.get("account_id") or "")
        if managed_id in {None, ""} or not account_id:
            return
        summary = self.repository.account_summary(account_id)
        current_balance = float(summary.get("balance") or 0.0) + float(profit)
        risk = self.rf_repository.record_account_outcome(
            managed_account_id=int(managed_id),
            profit=float(profit),
            current_balance=current_balance,
        )
        if risk["consecutive_losses"] >= self.risk_config.maximum_session_losses:
            self.repository.set_managed_account_enabled(int(managed_id), False)
            self.repository.set_managed_account_execution_status(
                int(managed_id),
                "session_loss_stop",
                f"Stopped after {risk['consecutive_losses']} consecutive losses",
            )
            self.logger.warning(
                "RF_ACCOUNT_SESSION_STOP account=%s consecutive_losses=%s",
                mask_account_id(account_id),
                risk["consecutive_losses"],
            )

    def _register_trade_cycle_outcome(self, outcome: str) -> None:
        if self.virtual_guard_config.enabled and str(outcome).lower() != "win":
            self.rf_repository.activate_after_demo_loss()
            self.logger.warning("RF_VIRTUAL_GUARD_ACTIVATED reason=demo_loss")

    def _record_real_cycle_outcome(self, outcome: str) -> None:
        del outcome

    def _register_master_market_outcome(self, symbol: str, outcome: str) -> None:
        del symbol, outcome

    def _complete_market_rotation_after_purchase(self, symbol: str) -> None:
        del symbol
