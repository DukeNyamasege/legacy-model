from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.repositories.rf_dir5_repository import (
    RECOVERY_PENDING,
    RFDir5Repository,
    VIRTUAL_MODE,
    VIRTUAL_WIN,
)
from app.services.telegram_alerts import TelegramAlertClient
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
    make_signal_event,
)
from enhanced_bot import TradingBot, mask_account_id, optional_float, optional_epoch_datetime


class _ExecutionDisabledBayesian:
    def update(self, _won: bool) -> None:
        return


class RFDir5TradingBot(TradingBot):
    """PUT-only production worker using the proven account/transport envelope."""

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__(config_path)
        self.rf_config = self.test2_config.rf_strategy
        self.risk_config = self.test2_config.risk
        self.virtual_config = self.test2_config.virtual_protection
        self.symbols = list(self.rf_config.markets)
        self.symbol = self.symbols[0]
        self.duration = self.rf_config.demo_duration_ticks
        self.duration_unit = "t"
        self.contract_type = "PUT"
        self.contract_barrier = ""
        self.pattern_length = self.rf_config.analysis_movements
        self.max_open_trade_seconds = max(self.max_open_trade_seconds, 30)

        history_size = self.rf_config.minimum_history_movements + 16
        for market in self.market_states.values():
            market.ticks_history = deque(maxlen=history_size)
            market.live_ticks_history = deque(maxlen=5)
            market.raw_tick_digits.clear()
        primary = self.market_states[self.symbol]
        self.ticks_history = primary.ticks_history
        self.live_ticks_history = primary.live_ticks_history
        self.raw_tick_digits = primary.raw_tick_digits

        self.rf_repository = RFDir5Repository(self.repository)
        self.telegram_alerts = TelegramAlertClient(
            self.test2_config.telegram,
            self.logger,
        )
        self.rf_decision_engine = RiseFallDecisionEngine(
            minimum_score=self.rf_config.minimum_directional_score,
            stale_signal_after_ms=self.rf_config.stale_signal_after_ms,
        )
        # The legacy global posterior remains import-compatible but is never updated or
        # consulted by RF-DIR5 execution.
        self.bayesian = _ExecutionDisabledBayesian()
        self.rf_candidate_queue: list[SignalEvent] = []
        self.rf_arbitration_task: asyncio.Task | None = None
        self.rf_supported_contracts: dict[str, set[str]] = {}
        self.rf_contract_validation_task: asyncio.Task | None = None
        self.rf_account_contract_tasks: dict[str, asyncio.Task] = {}
        self.rf_account_contract_validation_semaphore = asyncio.Semaphore(3)
        self.rf_account_supported_contracts: dict[tuple[str, str], set[str]] = {}
        self.rf_pending_recovery_registrations: dict[str, int] = {}
        self.rf_last_epoch: dict[str, int] = {}
        self.rf_last_tick_id: dict[str, str] = {}
        self.rf_last_purchase_monotonic = 0.0

        for state in self.clients.values():
            self._clear_recovery_state(state)
        self._save_state()
        self.logger.info(
            "RF_PUT5_ACTIVE version=%s direction=%s markets=%s demo_duration=%s "
            "cumulative_recovery=%s virtual_protection=%s trigger_actual_losses=%s",
            RF_DIR5_VERSION,
            self.rf_config.allowed_direction,
            ",".join(self.symbols),
            self.duration,
            self.risk_config.recovery_enabled,
            self.virtual_config.enabled,
            self.virtual_config.trigger_actual_losses,
        )

    async def _telegram_hourly_loop(self) -> None:
        await asyncio.sleep(self.test2_config.telegram.initial_delay_seconds)
        await self._send_virtual_protection_announcement_once()
        while self.is_running:
            sent = False
            try:
                report = self.repository.hourly_execution_report(
                    master_account_id=self._copytrading_master_account_id(),
                    window_minutes=60,
                )
                sent = await self.telegram_alerts.send_hourly_report(report)
            except Exception as exc:
                self.logger.warning(
                    "TELEGRAM_ALERT_FAILED error=%s",
                    type(exc).__name__,
                )
            retry_seconds = min(60, self.test2_config.telegram.interval_seconds)
            await asyncio.sleep(
                self.test2_config.telegram.interval_seconds
                if sent
                else retry_seconds
            )

    async def _send_virtual_protection_announcement_once(self) -> None:
        key = "telegram_announcement_virtual_loss_protection_v1"
        if self.repository.runtime_preference(key) == "sent":
            return
        text = "\n".join(
            (
                "Model update: Virtual loss protection is live.",
                "After 2 actual losses, affected accounts switch to $0 virtual checks until a virtual win.",
                "Then the next qualifying entry resumes real/demo recovery trading.",
                "Test our model: https://derivadmin.site/",
                "Join other traders and let's train the future.",
            )
        )
        try:
            if await self.telegram_alerts.send_announcement(text):
                self.repository.set_runtime_preference(key, "sent")
        except Exception as exc:
            self.logger.warning(
                "TELEGRAM_ANNOUNCEMENT_FAILED error=%s",
                type(exc).__name__,
            )

    async def run(self) -> None:
        if not self.telegram_alerts.enabled:
            await super().run()
            return
        alert_task = asyncio.create_task(
            self._telegram_hourly_loop(),
            name="telegram_hourly_alerts",
        )
        try:
            await super().run()
        finally:
            alert_task.cancel()
            try:
                await alert_task
            except asyncio.CancelledError:
                pass

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
        self.rf_pending_recovery_registrations.clear()
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

    def _public_history_count(self) -> int:
        return self.rf_config.minimum_history_movements + 1

    @staticmethod
    def _tick_identity(symbol: str, epoch: int, quote: Decimal) -> str:
        return f"{symbol}:{epoch}:{quote}"

    def _on_public_history(
        self,
        *,
        symbol: str,
        prices: list[Any],
        times: list[Any],
        pip_size: Any,
    ) -> None:
        market = self.market_states.get(symbol)
        if market is None:
            return
        if isinstance(pip_size, int) and pip_size >= 0:
            market.pip_size = pip_size
        market.ticks_history.clear()
        market.live_ticks_history.clear()
        for raw_price, raw_epoch in zip(prices, times):
            quote = Decimal(str(raw_price))
            epoch = int(raw_epoch)
            snapshot = {
                "quote": quote,
                "display": f"{quote:.{market.pip_size}f}",
                "epoch": epoch,
                "tick_id": self._tick_identity(symbol, epoch, quote),
                "last_digit": "-",
            }
            market.ticks_history.append(snapshot)
            market.live_ticks_history.append(snapshot)
        if market.ticks_history:
            latest = market.ticks_history[-1]
            self.rf_last_epoch[symbol] = int(latest["epoch"])
            self.rf_last_tick_id[symbol] = str(latest["tick_id"])

    def _on_public_connection_lost(self, error: Exception) -> None:
        if self.rf_contract_validation_task and not self.rf_contract_validation_task.done():
            self.rf_contract_validation_task.cancel()
        self.rf_contract_validation_task = None
        self.rf_supported_contracts.clear()

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
            if "PUT" not in types:
                self.logger.error(
                    "RF_MARKET_DISABLED symbol=%s required=PUT available=%s",
                    symbol,
                    sorted(types),
                )
            else:
                self.logger.info("RF_MARKET_VERIFIED symbol=%s contract=PUT", symbol)

    def _on_private_session_ready(self, session: Any) -> None:
        if all(
            "PUT" in (
                self.rf_account_supported_contracts.get((session.account_id, symbol), set())
            )
            for symbol in self.symbols
        ):
            self.logger.info(
                "RF_ACCOUNT_CONTRACTS_CACHED account=%s markets=%s",
                mask_account_id(session.account_id),
                len(self.symbols),
            )
            return
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
        async with self.rf_account_contract_validation_semaphore:
            for symbol in self.symbols:
                if "PUT" in (
                    self.rf_account_supported_contracts.get(
                        (session.account_id, symbol),
                        set(),
                    )
                ):
                    continue
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
                await asyncio.sleep(0.05)
        self.logger.info(
            "RF_ACCOUNT_CONTRACTS_VERIFIED account=%s markets=%s",
            mask_account_id(session.account_id),
            sum(
                    "PUT" in types
                for (account_id, _symbol), types in self.rf_account_supported_contracts.items()
                if account_id == session.account_id
            ),
        )

    def _account_supports_contract(
        self,
        *,
        account_id: str,
        symbol: str,
        contract_type: str,
    ) -> bool:
        required = str(contract_type or "").upper()
        account_types = self.rf_account_supported_contracts.get(
            (str(account_id), str(symbol)),
            set(),
        )
        if required in account_types:
            return True
        return required in self.rf_supported_contracts.get(str(symbol), set())

    def _render_live_ticks(self, note: str = "") -> None:
        handler = self._get_live_console_handler()
        market = self.market_states.get(self.live_market_symbol, self.market_states[self.symbol])
        if handler is None or not market.live_ticks_history:
            return
        quotes = [Decimal(str(item["quote"])) for item in market.live_ticks_history]
        moves = [later - earlier for earlier, later in zip(quotes[:-1], quotes[1:])]
        move_text = " | ".join(f"{value:+f}" for value in moves)
        quote_text = " | ".join(str(item.get("display", item["quote"])) for item in market.live_ticks_history)
        required_ticks = self.rf_config.minimum_history_movements + 1
        history_count = len(market.ticks_history)
        state = note or (
            f"SYNCING_HISTORY {history_count}/{required_ticks}"
            if history_count < required_ticks
            else "SCANNING"
        )
        handler.set_status(
            f"LIVE {market.symbol} | last5=[{quote_text}] | moves=[{move_text}] | "
            f"state={state}"
        )

    async def _on_tick(self, tick_data: dict[str, Any]) -> None:
        tick = tick_data.get("tick") or {}
        symbol = str(tick.get("symbol") or self.symbol)
        market = self.market_states.get(symbol)
        if market is None:
            return
        quote = Decimal(str(tick["quote"]))
        epoch = int(tick.get("epoch") or 0)
        tick_id = self._tick_identity(symbol, epoch, quote)
        self.live_market_symbol = symbol
        self._mark_tick_received(market)
        previous_epoch = self.rf_last_epoch.get(symbol, -1)
        out_of_order = epoch > 0 and previous_epoch > 0 and epoch < previous_epoch
        duplicate = tick_id == self.rf_last_tick_id.get(symbol)
        if out_of_order or duplicate:
            self.logger.warning(
                "RF_TICK_REJECTED symbol=%s reason=%s epoch=%s",
                symbol,
                "out_of_order" if out_of_order else "duplicate",
                epoch,
            )
            return
        if epoch > 0:
            self.rf_last_epoch[symbol] = epoch
        self.rf_last_tick_id[symbol] = tick_id

        display_value = f"{quote:.{market.pip_size}f}"
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
        for settled in self.rf_repository.settle_due_virtual_trades(
            symbol=symbol,
            tick_sequence=market.tick_sequence,
            exit_quote=quote,
            exit_epoch=epoch,
        ):
            self.logger.warning(
                "VIRTUAL_TRADE_SETTLED account=%s market=%s result=%s "
                "actual_financial_impact=0 recovery_debt=%.2f",
                settled["account"],
                settled["market"],
                settled["result"],
                float(settled["protection"].get("actual_recovery_debt") or 0.0),
            )
            if settled["result"] == VIRTUAL_WIN:
                self.logger.warning(
                    "VIRTUAL_WIN_CONFIRMED account=%s next_state=%s "
                    "next_action=REAL_RECOVERY_TRADE",
                    settled["account"],
                    settled["protection"].get("mode"),
                )

        if "PUT" not in self.rf_supported_contracts.get(symbol, set()):
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

        fall = detect_fall_candidate(
            features,
            minimum_directional_moves=self.rf_config.minimum_directional_moves,
            minimum_recent_directional_moves=(
                getattr(self.rf_config, "minimum_recent_directional_moves", 2)
            ),
            minimum_efficiency=self.rf_config.minimum_efficiency,
        )
        if not fall:
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

        direction = "FALL"
        quality_score = calculate_directional_score(
            features,
            direction=direction,
            volatility_ok=volatility_ok,
            exhaustion_ok=exhaustion_ok,
        )
        if quality_score < self.rf_config.minimum_directional_score:
            self.logger.info(
                "RF_SIGNAL_FILTERED symbol=%s score=%s minimum_score=%s",
                symbol,
                quality_score,
                self.rf_config.minimum_directional_score,
            )
            return
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

    def _candidate_rank_key(self, signal: SignalEvent) -> tuple[Any, ...]:
        return (
            -signal.quality_score,
            -signal.features.efficiency,
            -signal.features.impulse,
            -signal.generated_monotonic,
            signal.symbol,
        )

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
        return economics, requested, received

    async def _arbitrate_candidates(self) -> None:
        await asyncio.sleep(self.rf_config.candidate_window_ms / 1000.0)
        candidates = self.rf_candidate_queue
        self.rf_candidate_queue = []
        if not candidates:
            return

        fresh_candidates: list[SignalEvent] = []
        for signal in candidates:
            if self._market_rotation_blocks(signal.symbol):
                self._mark_rf_decision(
                    signal,
                    "SKIP_LOSS_MARKET_ROTATION",
                    "market suspended after master loss until another market wins",
                )
                self.logger.info(
                    "RF_MARKET_SUSPENDED signal_id=%s symbol=%s suspended_markets=%s",
                    signal.signal_id,
                    signal.symbol,
                    ",".join(self._loss_rotation_markets()),
                )
                continue
            market = self.market_states[signal.symbol]
            if market.tick_sequence != signal.tick_sequence:
                self._mark_rf_decision(
                    signal,
                    "SKIP_STALE_SIGNAL",
                    "new tick before candidate selection",
                )
                continue
            fresh_candidates.append(signal)

        if not fresh_candidates:
            return

        fresh_candidates.sort(key=self._candidate_rank_key)
        selected = fresh_candidates[0]
        for signal in fresh_candidates[1:]:
            self._mark_rf_decision(
                signal,
                "SKIP_MARKET_ARBITRATION",
                "another market ranked higher",
                selected=False,
            )

        self._prune_stale_pending_contracts("rf_pre_proposal")
        if (
            self.is_trading_locked
            or bool(self.pending_contracts_for_current_cycle)
        ):
            self._mark_rf_decision(
                selected,
                "SKIP_TRADING_LOCK",
                "existing contract cycle",
                selected=True,
            )
            return

        minimum_interval = float(self.rf_config.minimum_trade_interval_seconds)
        last_purchase = float(getattr(self, "rf_last_purchase_monotonic", 0.0))
        elapsed = time.monotonic() - last_purchase if last_purchase else minimum_interval
        if elapsed < minimum_interval:
            self._mark_rf_decision(
                selected,
                "SKIP_TRADE_SPACING",
                f"trade spacing {minimum_interval - elapsed:.1f}s remaining",
                selected=True,
            )
            return

        economics, _proposal_requested, proposal_received = (
            await self._proposal_for_duration(
                selected,
                self.rf_config.demo_duration_ticks,
            )
        )
        if economics is None:
            self._mark_rf_decision(
                selected,
                "SKIP_UNPROFITABLE_QUOTE",
                "invalid proposal",
                selected=True,
            )
            return

        selected.validated_edge = None
        selected.quality_score = calculate_directional_score(
            selected.features,
            direction=selected.direction,
            volatility_ok=True,
            exhaustion_ok=True,
            validated_edge=False,
        )
        selected.proposal_ask_price = economics.stake
        selected.proposal_payout = economics.payout
        selected.break_even_probability = economics.break_even_probability

        market = self.market_states[selected.symbol]
        signal_age_ms = (time.monotonic() - selected.generated_monotonic) * 1000.0
        proposal_age_ms = (time.monotonic() - proposal_received) * 1000.0
        if market.tick_sequence != selected.tick_sequence:
            self._mark_rf_decision(selected, "SKIP_STALE_SIGNAL", "new tick before purchase", selected=True)
            return

        self._prune_stale_pending_contracts("rf_pre_decision")
        execution_mode = self.environment
        decision = self.rf_decision_engine.decide(
            quality_score=selected.quality_score,
            signal_age_ms=signal_age_ms,
            proposal_age_ms=proposal_age_ms,
            proposal_economics=economics,
            execution_mode=execution_mode,
            trading_locked=(
                self.is_trading_locked
                or bool(self.pending_contracts_for_current_cycle)
            ),
        )
        if decision.action != "BUY_DEMO" or not self.test2_config.execution.demo_enabled:
            self._mark_rf_decision(selected, decision.action, ",".join(decision.reasons), selected=True)
            return
        if market.tick_sequence != selected.tick_sequence:
            self._mark_rf_decision(selected, "SKIP_STALE_SIGNAL", "tick changed after decision", selected=True)
            return
        await self._buy_selected_demo(selected, economics)

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
    ) -> None:
        eligible = self._eligible_purchase_accounts()
        skip_reasons: Counter[str] = Counter()
        stake_by_token: dict[str, float] = {}
        recovery_by_token: dict[str, bool] = {}
        managed_id_by_token: dict[str, int] = {}
        filtered: list[tuple[str, str]] = []
        virtual_opened: list[dict[str, Any]] = []
        virtual_waiting_accounts: set[str] = set()
        proposal_profit_ratio = economics.potential_profit / economics.stake
        for token, account_id in eligible:
            if not self._account_supports_contract(
                account_id=account_id,
                symbol=signal.symbol,
                contract_type=signal.contract_type,
            ):
                self.logger.warning(
                    "RF_ACCOUNT_SKIPPED account=%s reason=contract_not_verified symbol=%s type=%s",
                    mask_account_id(account_id),
                    signal.symbol,
                    signal.contract_type,
                )
                skip_reasons["contract_not_verified"] += 1
                continue
            state = self._client_state_for_token(token, account_id=account_id)
            managed_id = self._managed_account_id_for_token(token)
            summary = self.repository.account_summary(account_id)
            if managed_id is None:
                skip_reasons["missing_managed_account"] += 1
                continue
            if not summary.get("updated_at"):
                self._set_account_execution_status(
                    managed_id,
                    "reconnecting",
                    "Waiting for a current account balance",
                )
                self.logger.warning(
                    "RF_ACCOUNT_SKIPPED account=%s reason=balance_snapshot_unavailable; "
                    "healthy accounts continue",
                    mask_account_id(account_id),
                )
                skip_reasons["balance_snapshot_unavailable"] += 1
                continue
            plan = self.rf_repository.plan_stake(
                managed_account_id=managed_id,
                account_id_masked=mask_account_id(account_id),
                current_balance=float(summary.get("balance") or 0.0),
                requested_stake=0.50,
                proposal_profit_ratio=proposal_profit_ratio,
                recovery_enabled=self.risk_config.recovery_enabled,
                recovery_trigger_losses=self.risk_config.recovery_trigger_losses,
                minimum_stake=0.50,
                maximum_recovery_balance_fraction=(
                    self.risk_config.maximum_recovery_balance_fraction
                ),
                minimum_balance_reserve=self.risk_config.minimum_balance_reserve,
            )
            if plan.stake is None:
                if "balance" in plan.reason or "safety cap" in plan.reason:
                    self._set_account_execution_status(
                        managed_id,
                        "insufficient_balance",
                        plan.reason,
                    )
                    self.valid_clients = [
                        item for item in self.valid_clients if item[0] != token
                    ]
                    self.logger.warning(
                        "RF_ACCOUNT_QUARANTINED account=%s "
                        "status=insufficient_balance reason=%s; healthy accounts continue",
                        mask_account_id(account_id),
                        plan.reason,
                    )
                    skip_reasons["insufficient_balance"] += 1
                else:
                    self.logger.warning(
                        "RF_ACCOUNT_SKIPPED account=%s reason=%s; "
                        "retrying on a future quote",
                        mask_account_id(account_id),
                        plan.reason,
                    )
                    skip_reasons["risk_plan_blocked"] += 1
                continue
            protection = self.rf_repository.virtual_protection_for_account(
                managed_account_id=managed_id,
                account_id_masked=mask_account_id(account_id),
            )
            if self.virtual_config.enabled and protection.get("mode") == VIRTUAL_MODE:
                expected_payout = None
                if economics.stake > 0:
                    expected_payout = round(
                        (float(economics.payout) / float(economics.stake))
                        * float(plan.stake),
                        2,
                    )
                virtual = self.rf_repository.start_virtual_trade(
                    managed_account_id=managed_id,
                    account_id_masked=mask_account_id(account_id),
                    signal=signal,
                    configured_stake=float(state.get("base_stake", self.base_stake)),
                    simulated_stake=float(plan.stake),
                    expected_payout=expected_payout,
                )
                if virtual is not None:
                    virtual_opened.append(virtual)
                    self.logger.warning(
                        "VIRTUAL_TRADE_OPENED account=%s market=%s contract_type=%s "
                        "simulated_stake=%.2f expected_payout=%s actual_buy=false "
                        "actual_financial_impact=0 recovery_debt=%.2f",
                        mask_account_id(account_id),
                        signal.symbol,
                        signal.contract_type,
                        float(plan.stake),
                        (
                            f"{expected_payout:.2f}"
                            if expected_payout is not None
                            else "unavailable"
                        ),
                        float(virtual.get("recovery_debt") or 0.0),
                    )
                else:
                    virtual_waiting_accounts.add(mask_account_id(account_id))
                    self.logger.info(
                        "VIRTUAL_TRADE_WAITING account=%s signal_id=%s reason=active_virtual_observation",
                        mask_account_id(account_id),
                        signal.signal_id,
                    )
                continue
            if self.virtual_config.enabled and protection.get("mode") == RECOVERY_PENDING:
                self.logger.warning(
                    "REAL_RECOVERY_TRADE_ARMED account=%s actual_recovery_debt=%.2f",
                    mask_account_id(account_id),
                    float(protection.get("actual_recovery_debt") or 0.0),
                )
            filtered.append((token, account_id))
            stake_by_token[token] = plan.stake
            recovery_by_token[token] = plan.is_recovery
            managed_id_by_token[token] = managed_id
            if plan.is_recovery:
                self.logger.warning(
                    "RF_CUMULATIVE_RECOVERY_PLANNED account=%s debt=%.2f stake=%.2f "
                    "proposal_profit_ratio=%.5f",
                    mask_account_id(account_id),
                    plan.recovery_debt,
                    plan.stake,
                    proposal_profit_ratio,
                )
        if not filtered and virtual_opened:
            self.repository.consume_signal(signal.signal_id)
            signal.consumed = True
            self.repository.mark_signal(
                signal.signal_id,
                status="VIRTUAL_TRADE",
                purchase_requested=False,
                expected_account_masks=[
                    str(item.get("account") or "") for item in virtual_opened
                ],
                registered_account_masks=[],
            )
            self.rf_repository.set_signal_decision(
                signal.signal_id,
                "VIRTUAL_TRADE",
                "VIRTUAL_MODE_NO_PURCHASE",
                selected=True,
                validated_edge=signal.validated_edge,
            )
            self.logger.warning(
                "PURCHASE_BLOCKED_VIRTUAL_MODE signal_id=%s virtual_accounts=%s",
                signal.signal_id,
                len(virtual_opened),
            )
            return
        if not filtered and virtual_waiting_accounts:
            self.repository.consume_signal(signal.signal_id)
            signal.consumed = True
            self.repository.mark_signal(
                signal.signal_id,
                status="VIRTUAL_WAITING_SETTLEMENT",
                expected_account_masks=sorted(virtual_waiting_accounts),
                registered_account_masks=[],
            )
            self.rf_repository.set_signal_decision(
                signal.signal_id,
                "VIRTUAL_WAITING_SETTLEMENT",
                "ACTIVE_VIRTUAL_OBSERVATION",
                selected=True,
                validated_edge=signal.validated_edge,
            )
            return
        if not filtered:
            if not eligible:
                self._mark_rf_decision(
                    signal,
                    "SKIP_NO_ELIGIBLE_ACCOUNTS",
                    "all accounts disabled, settling, or unavailable",
                    selected=True,
                )
                return
            reason_summary = ",".join(
                f"{name}={count}" for name, count in sorted(skip_reasons.items())
            ) or "no risk-eligible accounts"
            if set(skip_reasons) == {"insufficient_balance"}:
                self._mark_rf_decision(
                    signal,
                    "SKIP_INSUFFICIENT_BALANCE",
                    reason_summary,
                    selected=True,
                )
                return
            if set(skip_reasons) == {"contract_not_verified"}:
                self._mark_rf_decision(
                    signal,
                    "SKIP_CONTRACT_NOT_VERIFIED",
                    reason_summary,
                    selected=True,
                )
                return
            self._mark_rf_decision(
                signal,
                "SKIP_NO_RISK_ELIGIBLE_ACCTS",
                reason_summary,
                selected=True,
            )
            return

        self.is_trading_locked = True
        self.pending_signal = signal
        try:
            if self.market_states[signal.symbol].tick_sequence != signal.tick_sequence:
                self._mark_rf_decision(signal, "SKIP_STALE_SIGNAL", "tick changed before transport", selected=True)
                return
            self.repository.consume_signal(signal.signal_id)
            signal.consumed = True
            expected_account_ids = {account_id for _token, account_id in filtered}
            self.repository.mark_signal(
                signal.signal_id,
                status="PURCHASE_REQUESTED",
                purchase_requested=True,
                expected_account_masks=[
                    mask_account_id(account_id) for account_id in expected_account_ids
                ],
            )
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
                if recovery_by_token.get(token):
                    self.rf_pending_recovery_registrations[token] = managed_id_by_token[token]
                try:
                    contract_id = await self._register_account_purchase(
                        signal=signal,
                        transaction=transaction,
                        token=token,
                        account_id=account_id,
                        stake_amount=stake,
                        profit_ratio=economics.potential_profit / economics.stake,
                        purchase_requested_at=requested_at,
                    )
                except Exception as exc:
                    self.logger.error(
                        "RF_ACCOUNT_REGISTRATION_FAILED account=%s error=%s",
                        mask_account_id(account_id),
                        exc,
                    )
                    continue
                finally:
                    self.rf_pending_recovery_registrations.pop(token, None)
                if contract_id is not None:
                    contracts.add(contract_id)
                    registered.add(account_id)
            self.pending_by_signal[signal.signal_id] = contracts
            if not contracts:
                self._mark_rf_decision(signal, "PURCHASE_FAILED", "demo purchase failed", selected=True)
                self.repository.mark_signal(
                    signal.signal_id,
                    status="PURCHASE_FAILED",
                    registered_account_masks=[],
                )
                return
            self._complete_market_rotation_after_purchase(signal.symbol)
            self._save_state()
            self.rf_last_purchase_monotonic = time.monotonic()
            missing_accounts = sorted(expected_account_ids - registered)
            final_status = "PURCHASE_PARTIAL" if missing_accounts else "PURCHASE_CONFIRMED"
            self.repository.mark_signal(
                signal.signal_id,
                status=final_status,
                purchase_confirmed=True,
                registered_account_masks=[
                    mask_account_id(account_id) for account_id in registered
                ],
            )
            if missing_accounts:
                self.logger.warning(
                    "RF_COPY_PURCHASE_PARTIAL signal_id=%s purchased=%s expected=%s "
                    "missing=%s; failed accounts were isolated and healthy accounts continue.",
                    signal.signal_id,
                    len(registered),
                    len(expected_account_ids),
                    [mask_account_id(account_id) for account_id in missing_accounts],
                )
            self.rf_repository.set_signal_decision(
                signal.signal_id,
                "BUY_DEMO",
                (
                    "DIRECT_DEMO_WITH_VIRTUAL_ACCOUNTS"
                    if virtual_opened
                    else "DIRECT_DEMO"
                ),
                selected=True,
                validated_edge=signal.validated_edge,
            )
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
        return {
            "buy": "1",
            "price": round(float(stake_amount), 2),
            "parameters": self._contract_parameters_for(
                signal,
                stake_amount,
                signal.duration_ticks,
            ),
        }

    def _eligible_purchase_accounts(self) -> list[tuple[str, str]]:
        eligible = [
            (token, account_id)
            for token, account_id in super()._eligible_purchase_accounts()
            if not self.sessions.get(token)
            or not self.sessions[token].pending_contracts
        ]
        eligible_tokens = {token for token, _account_id in eligible}
        for token, account_id in self.valid_clients:
            if token in eligible_tokens:
                continue
            session = self.sessions.get(token)
            if session is not None and session.pending_contracts:
                reason = "previous account contract is still settling"
            else:
                reason = "account is not eligible for this purchase cycle"
            self.logger.warning(
                "RF_ACCOUNT_EXCLUDED account=%s reason=%s; other accounts continue",
                mask_account_id(account_id),
                reason,
            )

        master_account_id = self._copytrading_master_account_id()
        if master_account_id and master_account_id not in {
            account_id for _token, account_id in eligible
        }:
            self.logger.warning(
                "RF_MASTER_ACCOUNT_EXCLUDED account=%s reason=not_purchase_eligible; "
                "copier execution continues",
                mask_account_id(master_account_id),
            )
        return eligible

    def _sync_running_status_after_validation(self) -> None:
        # RF-DIR5 has account-scoped start/stop controls. Obsolete global pause
        # flags must never stop otherwise eligible accounts.
        self.repository.set_status("RUNNING")

    def _planned_stake_for_account(
        self,
        token: str,
        account_id: str,
        profit_ratio: float,
    ) -> float:
        del profit_ratio
        state = self._client_state_for_token(token, account_id=account_id)
        return round(float(state.get("base_stake", self.base_stake)), 2)

    def _on_account_contract_registered(
        self,
        token: str,
        account_id: str,
        contract_id: int,
        stake_amount: float,
    ) -> None:
        managed_id = self.rf_pending_recovery_registrations.pop(token, None)
        if managed_id is None:
            return
        started = self.rf_repository.mark_recovery_attempt_started(managed_id)
        self.logger.warning(
            "RF_CUMULATIVE_RECOVERY_STARTED account=%s contract_id=%s stake=%.2f "
            "state_persisted=%s",
            mask_account_id(account_id),
            contract_id,
            stake_amount,
            started,
        )

    async def _purchase_accounts_by_stake(
        self,
        *,
        signal: SignalEvent,
        eligible_accounts: list[tuple[str, str]],
        stake_by_token: dict[str, float],
    ) -> list[dict[str, Any]]:
        guarded_accounts: list[tuple[str, str]] = []
        guarded_stakes: dict[str, float] = {}
        for token, account_id in eligible_accounts:
            managed_id = self._managed_account_id_for_token(token)
            protection = (
                self.rf_repository.virtual_protection_for_account(
                    managed_account_id=managed_id,
                    account_id_masked=mask_account_id(account_id),
                )
                if managed_id is not None
                else {"mode": "UNKNOWN"}
            )
            if protection.get("mode") == VIRTUAL_MODE:
                self.logger.error(
                    "PURCHASE_BLOCKED_VIRTUAL_MODE account=%s signal_id=%s",
                    mask_account_id(account_id),
                    signal.signal_id,
                )
                continue
            guarded_accounts.append((token, account_id))
            guarded_stakes[token] = stake_by_token[token]
        if not guarded_accounts:
            return []
        return await super()._purchase_accounts_by_stake(
            signal=signal,
            eligible_accounts=guarded_accounts,
            stake_by_token=guarded_stakes,
        )

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
        virtual_config = getattr(self, "virtual_config", None)
        risk = self.rf_repository.record_account_outcome(
            managed_account_id=int(managed_id),
            account_id_masked=mask_account_id(account_id),
            profit=float(profit),
            current_balance=current_balance,
            recovery_enabled=self.risk_config.recovery_enabled,
            recovery_trigger_losses=self.risk_config.recovery_trigger_losses,
            virtual_protection_enabled=getattr(virtual_config, "enabled", True),
            virtual_trigger_actual_losses=getattr(
                virtual_config,
                "trigger_actual_losses",
                2,
            ),
        )
        if risk.get("protection_state_changed") and risk.get("protection_mode") == VIRTUAL_MODE:
            self.logger.warning(
                "VIRTUAL_MODE_ENTERED account=%s reason=TWO_CONSECUTIVE_ACTUAL_LOSSES "
                "actual_losses=%s actual_recovery_debt=%.2f",
                mask_account_id(account_id),
                risk["consecutive_losses"],
                risk["recovery_loss_debt"],
            )
        if risk["settled_recovery_attempt"]:
            self.logger.warning(
                "RF_CUMULATIVE_RECOVERY_SETTLED account=%s result=%s profit=%.2f "
                "remaining_debt=%.2f recovery_pending=%s",
                mask_account_id(account_id),
                outcome.upper(),
                profit,
                risk["recovery_loss_debt"],
                risk["recovery_pending"],
            )
        elif risk["recovery_pending"]:
            self.logger.warning(
                "RF_CUMULATIVE_RECOVERY_ARMED account=%s consecutive_losses=%s debt=%.2f",
                mask_account_id(account_id),
                risk["consecutive_losses"],
                risk["recovery_loss_debt"],
            )
        if risk["consecutive_losses"] >= 3:
            self.logger.warning(
                "RF_ACCOUNT_CONTINUES_AFTER_LOSSES account=%s consecutive_losses=%s "
                "automatic_stop=false",
                mask_account_id(account_id),
                risk["consecutive_losses"],
            )

    def _register_trade_cycle_outcome(self, outcome: str) -> None:
        del outcome

    def _record_real_cycle_outcome(self, outcome: str) -> None:
        del outcome

    def _register_master_market_outcome(self, symbol: str, outcome: str) -> None:
        super()._register_master_market_outcome(symbol, outcome)

    def _complete_market_rotation_after_purchase(self, symbol: str) -> None:
        super()._complete_market_rotation_after_purchase(symbol)
