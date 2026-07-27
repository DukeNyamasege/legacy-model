from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.models import SystemModelTrade
from app.rf_dir5_bot import RFDir5TradingBot


# Strategy strengthening invariants. These deliberately sit *around* the existing
# five-move RF-PUT5 trigger; they do not replace its feature construction,
# volatility/exhaustion filters, Bayesian gate, HMM gate, or per-account virtual
# protection.
CONTEXT_MOVEMENTS = 15
CONTEXT_QUOTES = CONTEXT_MOVEMENTS + 1
CONTEXT_MIN_DOWN_MOVES = 9
CONTEXT_MIN_EFFICIENCY = 0.15
POST_LOSS_MIN_SCORE = 6
POST_LOSS_MIN_FIVE_MOVE_EFFICIENCY = 0.60
POST_LOSS_MIN_CONTEXT_DOWN_MOVES = 10


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    down_moves: int
    up_moves: int
    equal_moves: int
    net_move: Decimal
    efficiency: float

    @property
    def fall_agrees(self) -> bool:
        return (
            self.net_move < 0
            and self.down_moves >= CONTEXT_MIN_DOWN_MOVES
            and self.down_moves > self.up_moves
            and self.efficiency >= CONTEXT_MIN_EFFICIENCY
        )


def _context_snapshot(bot: RFDir5TradingBot, symbol: str) -> ContextSnapshot | None:
    market = bot.market_states.get(symbol)
    if market is None or len(market.ticks_history) < CONTEXT_QUOTES:
        return None
    quotes = [
        Decimal(str(item["quote"]))
        for item in list(market.ticks_history)[-CONTEXT_QUOTES:]
    ]
    movements = [later - earlier for earlier, later in zip(quotes[:-1], quotes[1:])]
    absolute_move = sum((abs(move) for move in movements), Decimal("0"))
    if absolute_move <= 0:
        return None
    net_move = sum(movements, Decimal("0"))
    return ContextSnapshot(
        down_moves=sum(move < 0 for move in movements),
        up_moves=sum(move > 0 for move in movements),
        equal_moves=sum(move == 0 for move in movements),
        net_move=net_move,
        efficiency=float(abs(net_move) / absolute_move),
    )


def _canonical_loss_streak(bot: RFDir5TradingBot, limit: int = 3) -> int:
    """Read the current strategy run only, so a new run starts with a clean streak."""
    try:
        with bot.repository.database.session() as session:
            rows = session.scalars(
                select(SystemModelTrade.outcome)
                .where(
                    SystemModelTrade.run_id == bot.repository.run_id,
                    SystemModelTrade.is_virtual.is_(False),
                    SystemModelTrade.settlement_timestamp.is_not(None),
                )
                .order_by(SystemModelTrade.settlement_timestamp.desc())
                .limit(max(1, int(limit)))
            ).all()
    except Exception:
        return 0

    streak = 0
    for value in rows:
        if str(value or "").upper() != "LOSS":
            break
        streak += 1
    return streak


def _passes_post_loss_gate(signal: Any, context: ContextSnapshot, loss_streak: int) -> bool:
    if loss_streak < 1:
        return True
    return bool(
        int(getattr(signal, "quality_score", 0)) >= POST_LOSS_MIN_SCORE
        and float(getattr(signal.features, "efficiency", 0.0))
        >= POST_LOSS_MIN_FIVE_MOVE_EFFICIENCY
        and context.down_moves >= POST_LOSS_MIN_CONTEXT_DOWN_MOVES
    )


def install_strict_streak_guard() -> None:
    """Install the 15-context -> 5-trigger -> 1-confirmation execution guard.

    Behaviour:
    * The existing RF-PUT5 five-move setup remains the candidate generator.
    * A candidate must agree with a 15-movement FALL context.
    * After one canonical monetary loss, the next candidate must also pass a
      stronger score/efficiency/context threshold.
    * A candidate is never purchased on its trigger tick. The very next valid
      market tick must move below the trigger quote. Only that confirmation tick
      is allowed to enter arbitration and the existing Bayesian/HMM/payout gates.
    * The existing per-account two-real-loss -> virtual mode -> two consecutive
      virtual wins protection remains authoritative for the hard streak break.
    """
    if getattr(RFDir5TradingBot, "_strict_streak_guard_installed", False):
        return

    original_schedule = RFDir5TradingBot._schedule_candidate_arbitration
    original_on_tick = RFDir5TradingBot._on_tick
    original_reset = RFDir5TradingBot._reset_session_runtime_state

    def _strict_schedule(self: RFDir5TradingBot) -> None:
        candidates = list(self.rf_candidate_queue)
        self.rf_candidate_queue = []
        if not candidates:
            return

        pending = getattr(self, "_strict_pending_confirmation", None)
        if not isinstance(pending, dict):
            pending = {}
            self._strict_pending_confirmation = pending

        for signal in candidates:
            context = _context_snapshot(self, signal.symbol)
            if context is None or not context.fall_agrees:
                reason = (
                    "15-tick FALL context unavailable"
                    if context is None
                    else (
                        "15-tick context disagrees: "
                        f"down={context.down_moves} up={context.up_moves} "
                        f"efficiency={context.efficiency:.3f} net={context.net_move}"
                    )
                )
                self._mark_rf_decision(
                    signal,
                    "SKIP_15T_CONTEXT",
                    reason,
                    selected=True,
                )
                continue

            loss_streak = _canonical_loss_streak(self)
            if not _passes_post_loss_gate(signal, context, loss_streak):
                self._mark_rf_decision(
                    signal,
                    "SKIP_POST_LOSS_STRICT_GATE",
                    (
                        f"after_loss={loss_streak} requires score>={POST_LOSS_MIN_SCORE}, "
                        f"five_efficiency>={POST_LOSS_MIN_FIVE_MOVE_EFFICIENCY:.2f}, "
                        f"context_down>={POST_LOSS_MIN_CONTEXT_DOWN_MOVES}; "
                        f"actual score={signal.quality_score} "
                        f"five_efficiency={signal.features.efficiency:.3f} "
                        f"context_down={context.down_moves}"
                    ),
                    selected=True,
                )
                continue

            # Only one unconfirmed candidate per market is retained. A later
            # candidate is inherently based on newer information and supersedes
            # an older unconfirmed setup.
            previous = pending.get(signal.symbol)
            if previous is not None:
                previous_signal = previous[0]
                self._mark_rf_decision(
                    previous_signal,
                    "SKIP_CONFIRMATION_SUPERSEDED",
                    "newer five-tick candidate arrived before confirmation",
                    selected=True,
                )

            pending[signal.symbol] = (signal, context, loss_streak)
            self.logger.info(
                "RF_STRICT_WAIT_CONFIRMATION signal_id=%s symbol=%s "
                "context=15t down=%s up=%s efficiency=%.3f post_loss_streak=%s",
                signal.signal_id,
                signal.symbol,
                context.down_moves,
                context.up_moves,
                context.efficiency,
                loss_streak,
            )

    async def _strict_on_tick(self: RFDir5TradingBot, tick_data: dict[str, Any]) -> None:
        tick = tick_data.get("tick") or {}
        symbol = str(tick.get("symbol") or self.symbol)
        market = self.market_states.get(symbol)
        before_sequence = int(market.tick_sequence) if market is not None else -1

        pending_map = getattr(self, "_strict_pending_confirmation", None)
        if not isinstance(pending_map, dict):
            pending_map = {}
            self._strict_pending_confirmation = pending_map
        waiting = pending_map.pop(symbol, None)

        await original_on_tick(self, tick_data)

        market = self.market_states.get(symbol)
        if waiting is None or market is None:
            return

        # Duplicate/out-of-order ticks are rejected by the original handler and
        # must not consume the one-tick confirmation opportunity.
        if int(market.tick_sequence) <= before_sequence:
            pending_map[symbol] = waiting
            return

        signal, context, loss_streak = waiting
        current_quote = Decimal(str(tick.get("quote")))
        trigger_quote = Decimal(str(signal.reference_entry_quote))
        if current_quote >= trigger_quote:
            self._mark_rf_decision(
                signal,
                "SKIP_1T_CONFIRMATION",
                (
                    "next tick did not confirm FALL: "
                    f"trigger={trigger_quote} confirmation={current_quote}"
                ),
                selected=True,
            )
            self.logger.info(
                "RF_STRICT_CONFIRMATION_FAILED signal_id=%s symbol=%s "
                "trigger=%s confirmation=%s",
                signal.signal_id,
                symbol,
                trigger_quote,
                current_quote,
            )
            return

        # From here forward the confirmation tick is the executable reference.
        # This prevents the 5-tick contract from being measured from a price that
        # existed one tick before the actual decision to enter.
        signal.reference_entry_quote = current_quote
        signal.tick_sequence = int(market.tick_sequence)
        signal.signal_tick_epoch = int(tick.get("epoch") or 0)
        signal.signal_tick_id = self._tick_identity(
            symbol,
            int(tick.get("epoch") or 0),
            current_quote,
        )
        signal.generated_monotonic = time.monotonic()

        self.rf_candidate_queue.append(signal)
        self.logger.info(
            "RF_STRICT_CONFIRMED signal_id=%s symbol=%s rule=15-5-1 "
            "context_down=%s context_efficiency=%.3f post_loss_streak=%s "
            "trigger=%s confirmation=%s",
            signal.signal_id,
            symbol,
            context.down_moves,
            context.efficiency,
            loss_streak,
            trigger_quote,
            current_quote,
        )
        # Call the original scheduler directly; calling the installed wrapper
        # would incorrectly demand a second confirmation tick.
        original_schedule(self)

    def _strict_reset(self: RFDir5TradingBot) -> None:
        original_reset(self)
        self._strict_pending_confirmation = {}

    RFDir5TradingBot._schedule_candidate_arbitration = _strict_schedule
    RFDir5TradingBot._on_tick = _strict_on_tick
    RFDir5TradingBot._reset_session_runtime_state = _strict_reset
    RFDir5TradingBot._strict_streak_guard_installed = True
