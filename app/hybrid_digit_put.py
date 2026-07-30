from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, select

from app.models import AccountRiskState, CandidateSignalRecord, ManagedAccount, SystemModelTrade, Trade, utc_now
from app.recovery import calculate_recovery_stake
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy.decision_engine import parse_proposal_economics
from app.strategy.rise_fall_strategy import shadow_outcome


PRIMARY_DIGITS = "PRIMARY_DIGITS"
PUT_RECOVERY = "PUT_RECOVERY"
HYBRID_STATE_KEY = "hybrid_o2u7_put_v1:state"
ACCOUNT_EPOCH_PREFIX = "hybrid_o2u7_put_v1:account_epoch:"
MIN_PROVIDER_STAKE = 0.35


@dataclass(slots=True)
class DigitSignal:
    signal_id: str
    run_id: str
    strategy_version: str
    symbol: str
    direction: str
    contract_type: str
    duration_ticks: int
    reference_entry_quote: Decimal
    quality_score: int
    signal_tick_epoch: int
    signal_tick_id: str
    generated_at: str
    generated_monotonic: float
    connection_session_id: str
    tick_sequence: int
    barrier: str
    trigger_name: str
    trigger_digits: tuple[int, ...]
    signal_last_digit: int
    p100: float
    p500: float
    p1000: float
    lower95: float
    weighted_probability: float
    consumed: bool = False
    proposal_ask_price: float | None = None
    proposal_payout: float | None = None
    break_even_probability: float | None = None
    validated_edge: float | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _final_digit(value: Any, pip_size: int) -> int:
    quote = Decimal(str(value))
    rendered = f"{quote:.{max(0, int(pip_size))}f}"
    for char in reversed(rendered):
        if char.isdigit():
            return int(char)
    raise ValueError(f"Could not derive final digit from quote {value!r}")


def _wilson_lower(successes: int, samples: int, z: float) -> float:
    if samples <= 0:
        return 0.0
    p = successes / samples
    z2 = z * z
    denominator = 1.0 + z2 / samples
    centre = p + z2 / (2.0 * samples)
    radius = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * samples)) / samples)
    return max(0.0, (centre - radius) / denominator)


def _state_default() -> dict[str, Any]:
    return {
        "mode": PRIMARY_DIGITS,
        "canonical_debt": 0.0,
        "participants": [],
        "primary_loss_signal": "",
        "recovery_started_at": "",
        "updated_at": _now_iso(),
    }


def _load_state(bot: RFDir5TradingBot) -> dict[str, Any]:
    raw = bot.repository.runtime_preference(HYBRID_STATE_KEY).strip()
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    state = _state_default()
    state.update(value)
    if state.get("mode") not in {PRIMARY_DIGITS, PUT_RECOVERY}:
        state["mode"] = PRIMARY_DIGITS
    state["canonical_debt"] = max(0.0, float(state.get("canonical_debt") or 0.0))
    state["participants"] = sorted(
        {int(item) for item in state.get("participants", []) if str(item).isdigit()}
    )
    return state


def _save_state(bot: RFDir5TradingBot) -> None:
    bot.hybrid_state["updated_at"] = _now_iso()
    bot.repository.set_runtime_preference(
        HYBRID_STATE_KEY,
        json.dumps(bot.hybrid_state, separators=(",", ":"), sort_keys=True),
    )


def _mode(bot: RFDir5TradingBot) -> str:
    return str(getattr(bot, "hybrid_state", {}).get("mode") or PRIMARY_DIGITS)


def _enabled_participants(bot: RFDir5TradingBot) -> set[int]:
    return {
        int(row.id)
        for row in bot.repository.list_managed_accounts()
        if bool(row.enabled)
    }


def _participant_needs_recovery(bot: RFDir5TradingBot, managed_id: int) -> bool:
    with bot.repository.database.session() as session:
        state = session.get(AccountRiskState, int(managed_id))
        if state is None:
            return False
        return bool(
            float(state.recovery_loss_debt or 0.0) > 0.009
            or state.recovery_pending
            or state.recovery_attempt_active
            or state.protection_mode in {"VIRTUAL_WAITING_FOR_WIN", "REAL_RECOVERY_PENDING"}
        )


def _recovery_account_ids(bot: RFDir5TradingBot) -> set[int]:
    return {
        managed_id
        for managed_id in _enabled_participants(bot)
        if _participant_needs_recovery(bot, managed_id)
    }


def _set_waiting_statuses(bot: RFDir5TradingBot) -> None:
    # Recovery is account-scoped. Never mark healthy accounts as waiting merely
    # because another account is observing virtual PUT contracts.
    del bot


def _return_to_primary(bot: RFDir5TradingBot, reason: str) -> None:
    if _mode(bot) == PRIMARY_DIGITS:
        return
    bot.hybrid_state.update(
        {
            "mode": PRIMARY_DIGITS,
            "canonical_debt": 0.0,
            "participants": [],
            "primary_loss_signal": "",
            "recovery_started_at": "",
        }
    )
    _save_state(bot)
    pending = getattr(bot, "_strict_pending_confirmation", None)
    if isinstance(pending, dict):
        pending.clear()
    bot.rf_candidate_queue.clear()
    bot.logger.warning("HYBRID_PRIMARY_RESUMED reason=%s", reason)


def _maybe_complete_recovery(bot: RFDir5TradingBot) -> None:
    if _mode(bot) != PUT_RECOVERY:
        return
    enabled = _enabled_participants(bot)
    if not enabled:
        _return_to_primary(bot, "no_enabled_recovery_participants")
        return
    needing = _recovery_account_ids(bot)
    if not needing:
        _return_to_primary(bot, "all_enabled_participant_debts_cleared")


def _enter_recovery(bot: RFDir5TradingBot, signal_id: str) -> None:
    with bot.repository.database.session() as session:
        participant_ids = {
            int(value)
            for value in session.scalars(
                select(Trade.managed_account_id).where(
                    Trade.signal_id == str(signal_id),
                    Trade.managed_account_id.is_not(None),
                )
            ).all()
            if value is not None
        }
    if not participant_ids:
        bot.logger.warning(
            "HYBRID_RECOVERY_NOT_ARMED signal_id=%s reason=no_registered_participants",
            signal_id,
        )
        return
    bot.hybrid_state.update(
        {
            "mode": PUT_RECOVERY,
            "canonical_debt": round(
                float(bot.hybrid_state.get("canonical_debt") or 0.0) + 0.50,
                2,
            ),
            "participants": sorted(participant_ids),
            "primary_loss_signal": str(signal_id),
            "recovery_started_at": _now_iso(),
        }
    )
    _save_state(bot)
    bot.hybrid_digit_candidates.clear()
    if bot.hybrid_digit_arbitration_task and not bot.hybrid_digit_arbitration_task.done():
        bot.hybrid_digit_arbitration_task.cancel()
    bot.hybrid_digit_arbitration_task = None
    _set_waiting_statuses(bot)
    bot.logger.warning(
        "HYBRID_PUT_RECOVERY_ARMED signal_id=%s participants=%s canonical_debt=%.2f "
        "next_action=WAIT_STRICT_15_5_1_PUT",
        signal_id,
        len(participant_ids),
        float(bot.hybrid_state["canonical_debt"]),
    )


def _apply_canonical_settlement(bot: RFDir5TradingBot, payload: dict[str, Any]) -> None:
    # Canonical model rows are accounting input only. AccountRiskState owns all
    # live recovery transitions; this callback must never create a global stop or
    # a second recovery debt ledger.
    bot.logger.info(
        "HYBRID_CANONICAL_SETTLED signal_id=%s contract_type=%s outcome=%s "
        "account_state=per_account",
        payload.get("signal_id", ""),
        payload.get("contract_type", ""),
        payload.get("outcome", ""),
    )


def _digit_metrics(digits: list[int], *, over: bool, barrier: int, z: float) -> dict[str, float]:
    def probability(window: int) -> float:
        sample = digits[-window:]
        if over:
            wins = sum(digit > barrier for digit in sample)
        else:
            wins = sum(digit < barrier for digit in sample)
        return wins / len(sample)

    p100 = probability(100)
    p500 = probability(500)
    p1000 = probability(1000)
    sample1000 = digits[-1000:]
    wins1000 = sum(
        digit > barrier if over else digit < barrier
        for digit in sample1000
    )
    lower = _wilson_lower(wins1000, len(sample1000), z)
    weighted = 0.50 * p100 + 0.30 * p500 + 0.20 * p1000
    return {
        "p100": p100,
        "p500": p500,
        "p1000": p1000,
        "lower95": lower,
        "weighted": weighted,
    }


def _make_digit_candidate(bot: RFDir5TradingBot, symbol: str, tick: dict[str, Any]) -> DigitSignal | None:
    market = bot.market_states[symbol]
    cfg = bot.test2_config.hybrid_strategy
    digits = [int(value) for value in market.raw_tick_digits if 0 <= int(value) <= 9]
    if len(digits) < max(cfg.windows):
        return None

    over = _digit_metrics(digits, over=True, barrier=cfg.over_barrier, z=cfg.confidence_z)
    under = _digit_metrics(digits, over=False, barrier=cfg.under_barrier, z=cfg.confidence_z)
    contract_type = "DIGITOVER"
    barrier = int(getattr(cfg, "primary_barrier", cfg.over_barrier))
    direction = f"OVER_{barrier}"
    metrics = over

    # Cheap pre-filter only. Live proposal economics below remain authoritative.
    if min(metrics["p100"], metrics["p500"], metrics["p1000"]) < 0.55:
        bot.logger.info(
            "HYBRID_DIGIT_PREFILTER symbol=%s barrier=%s p100=%.5f p500=%.5f p1000=%.5f "
            "threshold=0.55",
            symbol,
            barrier,
            metrics["p100"],
            metrics["p500"],
            metrics["p1000"],
        )
        return None

    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    tick_id = bot._tick_identity(symbol, epoch, quote)
    last_digits = tuple(digits[-10:])
    return DigitSignal(
        signal_id=str(uuid.uuid4()),
        run_id=bot.test2_config.model.run_id,
        strategy_version=cfg.version,
        symbol=symbol,
        direction=direction,
        contract_type=contract_type,
        duration_ticks=cfg.duration_ticks,
        reference_entry_quote=quote,
        quality_score=1,
        signal_tick_epoch=epoch,
        signal_tick_id=tick_id,
        generated_at=_now_iso(),
        generated_monotonic=time.monotonic(),
        connection_session_id=bot.connection_session_id,
        tick_sequence=int(market.tick_sequence),
        barrier=str(barrier),
        trigger_name=cfg.version,
        trigger_digits=last_digits,
        signal_last_digit=last_digits[-1],
        p100=metrics["p100"],
        p500=metrics["p500"],
        p1000=metrics["p1000"],
        lower95=metrics["lower95"],
        weighted_probability=metrics["weighted"],
    )


async def _digit_proposal(bot: RFDir5TradingBot, signal: DigitSignal):
    requested = time.monotonic()
    response = await bot.public_client.send_request(
        bot._proposal_request_for(signal, 0.50, signal.duration_ticks)
    )
    received = time.monotonic()
    if "error" in response:
        return signal, None
    try:
        economics = parse_proposal_economics(
            response,
            stake=0.50,
            predicted_probability=signal.weighted_probability,
            requested_monotonic=requested,
            received_monotonic=received,
            commission_in_ask=True,
        )
    except (TypeError, ValueError):
        return signal, None
    return signal, economics


async def _arbitrate_digits(bot: RFDir5TradingBot) -> None:
    cfg = bot.test2_config.hybrid_strategy
    await asyncio.sleep(cfg.candidate_window_ms / 1000.0)
    queued = list(bot.hybrid_digit_candidates.values())
    bot.hybrid_digit_candidates.clear()
    if _mode(bot) != PRIMARY_DIGITS or not queued:
        return

    bot._prune_stale_pending_contracts("hybrid_digit_pre_proposal")
    if bot.is_trading_locked or bool(bot.pending_contracts_for_current_cycle):
        for candidate in queued:
            bot.repository.mark_signal(candidate.signal_id, status="SKIP_TRADING_LOCK")
        return

    fresh = [
        candidate
        for candidate in queued
        if bot.market_states[candidate.symbol].tick_sequence == candidate.tick_sequence
    ]
    if not fresh:
        return

    proposals = await asyncio.gather(*(_digit_proposal(bot, signal) for signal in fresh))
    qualified: list[tuple[float, DigitSignal, Any]] = []
    for signal, economics in proposals:
        if economics is None:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_UNPROFITABLE_QUOTE")
            continue
        break_even = float(economics.break_even_probability)
        signal.proposal_ask_price = float(economics.stake)
        signal.proposal_payout = float(economics.payout)
        signal.break_even_probability = break_even
        margins = (
            signal.p100 - break_even - cfg.p100_edge,
            signal.p500 - break_even - cfg.p500_edge,
            signal.p1000 - break_even - cfg.p1000_edge,
            signal.lower95 - break_even + 0.02,
        )
        edge = min(margins)
        signal.validated_edge = edge
        if edge <= 0:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_DIGIT_EDGE")
            bot.logger.info(
                "HYBRID_DIGIT_SKIP signal_id=%s symbol=%s type=%s barrier=%s "
                "p100=%.5f p500=%.5f p1000=%.5f lower95=%.5f break_even=%.5f edge=%.5f",
                signal.signal_id,
                signal.symbol,
                signal.contract_type,
                signal.barrier,
                signal.p100,
                signal.p500,
                signal.p1000,
                signal.lower95,
                break_even,
                edge,
            )
            continue
        if bot.market_states[signal.symbol].tick_sequence != signal.tick_sequence:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_STALE_SIGNAL", stale=True)
            continue
        qualified.append((edge, signal, economics))

    if not qualified:
        return
    qualified.sort(key=lambda item: (-item[0], -item[1].weighted_probability, item[1].symbol))
    edge, selected, economics = qualified[0]
    for _other_edge, other, _other_economics in qualified[1:]:
        bot.repository.mark_signal(other.signal_id, status="SKIP_MARKET_ARBITRATION")

    bot.logger.warning(
        "HYBRID_DIGIT_SELECTED signal_id=%s symbol=%s type=%s barrier=%s edge=%.5f "
        "p100=%.5f p500=%.5f p1000=%.5f lower95=%.5f break_even=%.5f",
        selected.signal_id,
        selected.symbol,
        selected.contract_type,
        selected.barrier,
        edge,
        selected.p100,
        selected.p500,
        selected.p1000,
        selected.lower95,
        float(selected.break_even_probability or 0.0),
    )

    # Primary digit trades always start from each user's configured base stake.
    # Old RF recovery state is cleared lazily once per account for this new model
    # epoch; historical Trade rows are untouched.
    for token, account_id in bot._eligible_purchase_accounts():
        managed_id = bot._managed_account_id_for_token(token)
        if managed_id is None:
            continue
        epoch_key = f"{ACCOUNT_EPOCH_PREFIX}{managed_id}"
        if bot.repository.runtime_preference(epoch_key) != cfg.version:
            bot.repository.resume_managed_account(int(managed_id), reset_recovery=True)
            bot.repository.set_runtime_preference(epoch_key, cfg.version)
            bot.logger.info(
                "HYBRID_ACCOUNT_BASELINE_INITIALIZED account=%s model=%s",
                bot.repository.account_summary(account_id, managed_account_id=managed_id).get("account", "***"),
                cfg.version,
            )

    original_recovery_enabled = bool(bot.risk_config.recovery_enabled)
    try:
        bot.risk_config.recovery_enabled = False
        await bot._buy_selected_accounts(selected, economics)
    finally:
        bot.risk_config.recovery_enabled = original_recovery_enabled


def install_hybrid_digit_put_strategy() -> None:
    """Install O2/U7 primary trading with the existing strict PUT model as recovery.

    Installation must occur after ``install_strict_streak_guard`` so the captured
    PUT scheduler below already includes the 15 -> 5 -> 1 confirmation logic.
    """
    if getattr(RFDir5TradingBot, "_hybrid_digit_put_installed", False):
        return

    from app.repositories.test2_repository import Test2Repository

    strict_init = RFDir5TradingBot.__init__
    strict_on_tick = RFDir5TradingBot._on_tick
    strict_schedule = RFDir5TradingBot._schedule_candidate_arbitration
    strict_arbitrate = RFDir5TradingBot._arbitrate_candidates
    original_history = RFDir5TradingBot._on_public_history
    original_contract_parameters = RFDir5TradingBot._contract_parameters_for
    original_eligible = RFDir5TradingBot._eligible_purchase_accounts
    original_update_recovery = RFDir5TradingBot._update_client_recovery_state
    original_record_tick = Test2Repository.record_tick

    def hybrid_init(self: RFDir5TradingBot, config_path: str | None = None) -> None:
        strict_init(self, config_path)
        cfg = self.test2_config.hybrid_strategy
        self.hybrid_state = _load_state(self)
        if _mode(self) == PUT_RECOVERY:
            try:
                if not _recovery_account_ids(self):
                    _return_to_primary(self, "startup_no_recovery_needed")
            except Exception:
                pass
        self.hybrid_digit_candidates: dict[str, DigitSignal] = {}
        self.hybrid_digit_arbitration_task: asyncio.Task | None = None
        self.hybrid_last_waiting_refresh = 0.0
        self.hybrid_primary_symbols = (str(cfg.primary_markets[0]),)
        self.hybrid_recovery_symbols = (str(cfg.recovery_markets[0]),)
        # V4 intentionally subscribes to one underlying for both the primary
        # digit contract and the PUT recovery contract.
        self.symbols = list(dict.fromkeys((*self.hybrid_primary_symbols, *self.hybrid_recovery_symbols)))
        self.repository._hybrid_digit_by_symbol = {}
        self.repository._hybrid_settlement_callback = lambda payload: _apply_canonical_settlement(self, payload)
        self.logger.warning(
            "HYBRID_OVER2_PUT_ACTIVE version=%s mode=PER_ACCOUNT primary_market=%s "
            "recovery_market=%s primary_contract=DIGITOVER barrier=2 "
            "daily_loss_cap=false global_recovery_stop=false",
            cfg.version,
            self.hybrid_primary_symbols[0],
            self.hybrid_recovery_symbols[0],
        )

    def hybrid_history(self: RFDir5TradingBot, *, symbol: str, prices: list[Any], times: list[Any], pip_size: Any) -> None:
        original_history(self, symbol=symbol, prices=prices, times=times, pip_size=pip_size)
        market = self.market_states.get(symbol)
        if market is None:
            return
        digits = []
        for price in prices:
            try:
                digits.append(_final_digit(price, market.pip_size))
            except (ValueError, ArithmeticError):
                continue
        market.raw_tick_digits = deque(digits[-10000:], maxlen=10000)
        if symbol == self.symbol:
            self.raw_tick_digits = market.raw_tick_digits
        self.logger.info("HYBRID_DIGIT_HISTORY_READY symbol=%s digits=%s", symbol, len(digits))

    async def hybrid_on_tick(self: RFDir5TradingBot, tick_data: dict[str, Any]) -> None:
        tick = tick_data.get("tick") or {}
        symbol = str(tick.get("symbol") or self.symbol)
        market = self.market_states.get(symbol)
        digit = None
        if market is not None:
            try:
                digit = _final_digit(tick.get("quote"), market.pip_size)
            except (ValueError, ArithmeticError):
                digit = None
        if digit is not None:
            self.repository._hybrid_digit_by_symbol[symbol] = digit

        await strict_on_tick(self, tick_data)

        market = self.market_states.get(symbol)
        if market is None or digit is None:
            return
        market.raw_tick_digits.append(int(digit))

        if symbol not in self.hybrid_primary_symbols:
            return
        if self.is_trading_locked or bool(self.pending_contracts_for_current_cycle):
            return
        candidate = _make_digit_candidate(self, symbol, tick)
        if candidate is None:
            return
        self.repository.record_candidate(candidate)
        previous = self.hybrid_digit_candidates.get(symbol)
        if previous is not None:
            self.repository.mark_signal(previous.signal_id, status="SKIP_SUPERSEDED")
        self.hybrid_digit_candidates[symbol] = candidate
        if self.hybrid_digit_arbitration_task is None or self.hybrid_digit_arbitration_task.done():
            task = asyncio.create_task(_arbitrate_digits(self), name="hybrid_digit_arbitration")
            self.hybrid_digit_arbitration_task = task

            def done(completed: asyncio.Task) -> None:
                if self.hybrid_digit_arbitration_task is completed:
                    self.hybrid_digit_arbitration_task = None
                try:
                    completed.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    self.logger.exception("HYBRID_DIGIT_ARBITRATION_FAILED")

            task.add_done_callback(done)

    def hybrid_schedule(self: RFDir5TradingBot) -> None:
        # PUT candidates are useful whenever at least one enabled account is in
        # virtual protection or has completed its two virtual wins. Other
        # accounts continue receiving the primary OVER_2 stream independently.
        recovery_symbols = set(self.hybrid_recovery_symbols)
        rejected = [s for s in self.rf_candidate_queue if s.symbol not in recovery_symbols]
        self.rf_candidate_queue = [s for s in self.rf_candidate_queue if s.symbol in recovery_symbols]
        for signal in rejected:
            self._mark_rf_decision(signal, "SKIP_RECOVERY_MARKET", "market not enabled for PUT recovery")
        if not _recovery_account_ids(self):
            for signal in self.rf_candidate_queue:
                self._mark_rf_decision(
                    signal,
                    "SKIP_RECOVERY_STATE_SYNC",
                    "waiting for participant loss/debt settlement before PUT recovery",
                    selected=True,
                )
            self.rf_candidate_queue = []
            _maybe_complete_recovery(self)
            return
        strict_schedule(self)

    async def hybrid_arbitrate(self: RFDir5TradingBot) -> None:
        if not _recovery_account_ids(self):
            self.rf_candidate_queue.clear()
            return
        await strict_arbitrate(self)

    def hybrid_contract_parameters(
        self: RFDir5TradingBot,
        signal: Any,
        stake_amount: float,
        duration_ticks: int,
    ) -> dict[str, Any]:
        values = original_contract_parameters(self, signal, stake_amount, duration_ticks)
        if str(getattr(signal, "contract_type", "")).upper() in {"DIGITOVER", "DIGITUNDER"}:
            values["barrier"] = str(getattr(signal, "barrier", ""))
        return values

    def hybrid_eligible(self: RFDir5TradingBot) -> list[tuple[str, str]]:
        return original_eligible(self)

    def hybrid_update_recovery(self: RFDir5TradingBot, state: dict[str, Any], *, outcome: str, profit: float) -> None:
        original_update_recovery(self, state, outcome=outcome, profit=profit)
        # AccountRiskState is authoritative. There is no global completion
        # transition that can pause healthy accounts.

    def hybrid_record_tick(
        self: Test2Repository,
        *,
        sequence_id: int,
        symbol: str,
        epoch: int,
        tick_id: str,
        quote: float,
        final_digit: int,
        connection_session_id: str,
    ) -> None:
        digit = int(final_digit)
        if digit < 0:
            try:
                digit = int(getattr(self, "_hybrid_digit_by_symbol", {}).get(symbol, digit))
            except (TypeError, ValueError):
                pass
        original_record_tick(
            self,
            sequence_id=sequence_id,
            symbol=symbol,
            epoch=epoch,
            tick_id=tick_id,
            quote=quote,
            final_digit=digit,
            connection_session_id=connection_session_id,
        )

    def hybrid_settle_system(
        self: Test2Repository,
        *,
        symbol: str,
        tick_sequence: int,
        exit_spot: float,
    ) -> list[dict[str, Any]]:
        digit_map = getattr(self, "_hybrid_digit_by_symbol", {})
        exit_digit = digit_map.get(str(symbol))
        now = utc_now()
        settled: list[dict[str, Any]] = []
        with self.database.session() as session:
            rows = session.scalars(
                select(SystemModelTrade)
                .where(
                    SystemModelTrade.run_id == self.run_id,
                    SystemModelTrade.symbol == str(symbol),
                    SystemModelTrade.outcome.is_(None),
                    SystemModelTrade.expiry_tick_sequence <= int(tick_sequence),
                    exists().where(Trade.signal_id == SystemModelTrade.signal_id),
                )
                .with_for_update()
            ).all()
            for trade in rows:
                contract_type = str(trade.contract_type or "").upper()
                if contract_type in {"DIGITOVER", "DIGITUNDER"}:
                    if exit_digit is None:
                        continue
                    candidate = session.get(CandidateSignalRecord, trade.signal_id)
                    if candidate is None or str(candidate.barrier or "") == "":
                        continue
                    barrier = int(str(candidate.barrier))
                    if contract_type == "DIGITOVER":
                        outcome = "WIN" if int(exit_digit) > barrier else "LOSS"
                    else:
                        outcome = "WIN" if int(exit_digit) < barrier else "LOSS"
                else:
                    outcome = shadow_outcome(
                        trade.direction,
                        Decimal(str(trade.entry_spot)),
                        Decimal(str(exit_spot)),
                    )
                trade.outcome = outcome
                trade.is_virtual = False
                trade.exit_spot = float(exit_spot)
                trade.settlement_timestamp = now
                ratio = max(0.0, float(trade.expected_profit_ratio or 0.0))
                trade.fixed_stake_profit = ratio * 0.50 if outcome == "WIN" else -0.50
                payload = {
                    "signal_id": trade.signal_id,
                    "outcome": outcome,
                    "is_virtual": False,
                    "contract_type": contract_type,
                    "expected_profit_ratio": ratio,
                    "exit_digit": int(exit_digit) if exit_digit is not None else None,
                }
                settled.append(payload)
        callback = getattr(self, "_hybrid_settlement_callback", None)
        if callable(callback):
            for payload in settled:
                callback(payload)
        return settled

    RFDir5TradingBot.__init__ = hybrid_init
    RFDir5TradingBot._on_public_history = hybrid_history
    RFDir5TradingBot._on_tick = hybrid_on_tick
    RFDir5TradingBot._schedule_candidate_arbitration = hybrid_schedule
    RFDir5TradingBot._arbitrate_candidates = hybrid_arbitrate
    RFDir5TradingBot._contract_parameters_for = hybrid_contract_parameters
    RFDir5TradingBot._eligible_purchase_accounts = hybrid_eligible
    RFDir5TradingBot._update_client_recovery_state = hybrid_update_recovery
    Test2Repository.record_tick = hybrid_record_tick
    Test2Repository.settle_due_system_model_trades = hybrid_settle_system
    RFDir5TradingBot._hybrid_digit_put_installed = True
