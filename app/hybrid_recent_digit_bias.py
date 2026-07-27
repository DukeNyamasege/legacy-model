from __future__ import annotations

import asyncio
import time
import uuid
from decimal import Decimal
from typing import Any

import app.hybrid_digit_put as hybrid
from app.rf_dir5_bot import RFDir5TradingBot


RECENT_WINDOW = 20
MIN_RECENT_HIT_RATE = 0.75
MIN_BIAS_GAP = 0.05
MIN_LIVE_EDGE = 0.02
STRATEGY_VERSION = "HYBRID-O2-U7-RECENT20-PUTREC-V2"
# candidate_signals.trigger_name is VARCHAR(30). Keep the durable trigger code
# compact while retaining STRATEGY_VERSION as the full runtime/model identity.
LEDGER_TRIGGER_NAME = "O2U7-RECENT20-PUTREC-V2"


def _recent_bias_metrics(digits: list[int]) -> dict[str, float]:
    sample = [int(value) for value in digits[-RECENT_WINDOW:] if 0 <= int(value) <= 9]
    if len(sample) < RECENT_WINDOW:
        return {}

    over_hits = sum(digit > 2 for digit in sample)
    under_hits = sum(digit < 7 for digit in sample)
    over_rate = over_hits / RECENT_WINDOW
    under_rate = under_hits / RECENT_WINDOW

    # OVER 2 loses on 0/1/2. UNDER 7 loses on 7/8/9. The preferred contract is
    # therefore the side whose losing tail is currently less common.
    low_tail = sum(digit <= 2 for digit in sample)
    high_tail = sum(digit >= 7 for digit in sample)

    return {
        "over_rate": over_rate,
        "under_rate": under_rate,
        "bias_gap": abs(over_rate - under_rate),
        "low_tail_rate": low_tail / RECENT_WINDOW,
        "high_tail_rate": high_tail / RECENT_WINDOW,
    }


def _make_recent_candidate(
    bot: RFDir5TradingBot,
    symbol: str,
    tick: dict[str, Any],
) -> hybrid.DigitSignal | None:
    market = bot.market_states[symbol]
    cfg = bot.test2_config.hybrid_strategy
    digits = [int(value) for value in market.raw_tick_digits if 0 <= int(value) <= 9]
    metrics = _recent_bias_metrics(digits)
    if not metrics:
        return None

    over_rate = float(metrics["over_rate"])
    under_rate = float(metrics["under_rate"])
    bias_gap = float(metrics["bias_gap"])

    if over_rate > under_rate:
        contract_type = "DIGITOVER"
        barrier = int(cfg.over_barrier)
        direction = f"OVER_{barrier}"
        selected_rate = over_rate
        opposite_rate = under_rate
    elif under_rate > over_rate:
        contract_type = "DIGITUNDER"
        barrier = int(cfg.under_barrier)
        direction = f"UNDER_{barrier}"
        selected_rate = under_rate
        opposite_rate = over_rate
    else:
        return None

    minimum_rate = float(getattr(cfg, "minimum_recent_hit_rate", MIN_RECENT_HIT_RATE))
    minimum_gap = float(getattr(cfg, "minimum_bias_gap", MIN_BIAS_GAP))
    if selected_rate + 1e-12 < minimum_rate or bias_gap + 1e-12 < minimum_gap:
        return None

    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    tick_id = bot._tick_identity(symbol, epoch, quote)
    trigger_digits = tuple(digits[-RECENT_WINDOW:])

    # The legacy DigitSignal probability slots are retained for compatibility with
    # the existing candidate ledger. They now carry recent-bias values only; the
    # V1 100/500/1000 decision gate is not used by this controller.
    return hybrid.DigitSignal(
        signal_id=str(uuid.uuid4()),
        run_id=bot.test2_config.model.run_id,
        strategy_version=STRATEGY_VERSION,
        symbol=symbol,
        direction=direction,
        contract_type=contract_type,
        duration_ticks=int(cfg.duration_ticks),
        reference_entry_quote=quote,
        quality_score=max(1, min(10, int(round((selected_rate - 0.70) * 100)))),
        signal_tick_epoch=epoch,
        signal_tick_id=tick_id,
        generated_at=hybrid._now_iso(),
        generated_monotonic=time.monotonic(),
        connection_session_id=bot.connection_session_id,
        tick_sequence=int(market.tick_sequence),
        barrier=str(barrier),
        trigger_name=LEDGER_TRIGGER_NAME,
        trigger_digits=trigger_digits,
        signal_last_digit=trigger_digits[-1],
        p100=selected_rate,
        p500=opposite_rate,
        p1000=bias_gap,
        lower95=0.0,
        weighted_probability=selected_rate,
    )


async def _arbitrate_recent_digits(bot: RFDir5TradingBot) -> None:
    cfg = bot.test2_config.hybrid_strategy
    await asyncio.sleep(float(cfg.candidate_window_ms) / 1000.0)
    queued = list(bot.hybrid_digit_candidates.values())
    bot.hybrid_digit_candidates.clear()

    if hybrid._mode(bot) != hybrid.PRIMARY_DIGITS or not queued:
        return

    bot._prune_stale_pending_contracts("hybrid_recent_digit_pre_proposal")
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

    proposals = await asyncio.gather(
        *(hybrid._digit_proposal(bot, signal) for signal in fresh)
    )

    minimum_edge = float(getattr(cfg, "minimum_live_edge", MIN_LIVE_EDGE))
    minimum_gap = float(getattr(cfg, "minimum_bias_gap", MIN_BIAS_GAP))
    qualified: list[tuple[float, float, hybrid.DigitSignal, Any]] = []

    for signal, economics in proposals:
        if economics is None:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_UNPROFITABLE_QUOTE")
            continue

        selected_rate = float(signal.weighted_probability)
        opposite_rate = float(signal.p500)
        bias_gap = float(signal.p1000)
        break_even = float(economics.break_even_probability)
        live_edge = selected_rate - break_even

        signal.proposal_ask_price = float(economics.stake)
        signal.proposal_payout = float(economics.payout)
        signal.break_even_probability = break_even
        signal.validated_edge = live_edge

        if live_edge + 1e-12 < minimum_edge or bias_gap + 1e-12 < minimum_gap:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_RECENT_DIGIT_EDGE")
            bot.logger.info(
                "HYBRID_RECENT_DIGIT_SKIP signal_id=%s symbol=%s type=%s barrier=%s "
                "window=%s selected_rate=%.5f opposite_rate=%.5f bias_gap=%.5f "
                "break_even=%.5f live_edge=%.5f",
                signal.signal_id,
                signal.symbol,
                signal.contract_type,
                signal.barrier,
                RECENT_WINDOW,
                selected_rate,
                opposite_rate,
                bias_gap,
                break_even,
                live_edge,
            )
            continue

        if bot.market_states[signal.symbol].tick_sequence != signal.tick_sequence:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_STALE_SIGNAL", stale=True)
            continue

        # Rank mainly by live payout edge, then by how clearly one losing tail is
        # suppressed relative to the other. Only one market wins each cycle.
        score = live_edge + 0.25 * bias_gap
        qualified.append((score, selected_rate, signal, economics))

    if not qualified:
        return

    qualified.sort(key=lambda item: (-item[0], -item[1], item[2].symbol))
    score, _selected_rate, selected, economics = qualified[0]

    for _score, _rate, other, _other_economics in qualified[1:]:
        bot.repository.mark_signal(other.signal_id, status="SKIP_MARKET_ARBITRATION")

    bot.logger.warning(
        "HYBRID_RECENT_DIGIT_SELECTED signal_id=%s symbol=%s type=%s barrier=%s "
        "window=%s selected_rate=%.5f opposite_rate=%.5f bias_gap=%.5f "
        "break_even=%.5f live_edge=%.5f score=%.5f",
        selected.signal_id,
        selected.symbol,
        selected.contract_type,
        selected.barrier,
        RECENT_WINDOW,
        float(selected.weighted_probability),
        float(selected.p500),
        float(selected.p1000),
        float(selected.break_even_probability or 0.0),
        float(selected.validated_edge or 0.0),
        score,
    )

    # New primary trades always use each trader's own configured base stake. Any
    # model recovery remains isolated to the PUT_RECOVERY state.
    for token, account_id in bot._eligible_purchase_accounts():
        managed_id = bot._managed_account_id_for_token(token)
        if managed_id is None:
            continue
        epoch_key = f"{hybrid.ACCOUNT_EPOCH_PREFIX}{managed_id}"
        if bot.repository.runtime_preference(epoch_key) != STRATEGY_VERSION:
            bot.repository.resume_managed_account(int(managed_id), reset_recovery=True)
            bot.repository.set_runtime_preference(epoch_key, STRATEGY_VERSION)
            bot.logger.info(
                "HYBRID_ACCOUNT_BASELINE_INITIALIZED account=%s model=%s",
                bot.repository.account_summary(
                    account_id,
                    managed_account_id=managed_id,
                ).get("account", "***"),
                STRATEGY_VERSION,
            )

    original_recovery_enabled = bool(bot.risk_config.recovery_enabled)
    try:
        bot.risk_config.recovery_enabled = False
        await bot._buy_selected_accounts(selected, economics)
    finally:
        bot.risk_config.recovery_enabled = original_recovery_enabled


def install_recent_digit_bias_strategy() -> None:
    """Replace the V1 multi-window O2/U7 entry gate with one recent-digit bias.

    This intentionally does not touch the hybrid state machine or strict PUT
    scheduler. PUT therefore remains recovery-only and still uses the established
    15 -> 5 -> 1 confirmation conditions.
    """
    if getattr(hybrid, "_recent_digit_bias_installed", False):
        return

    hybrid._make_digit_candidate = _make_recent_candidate
    hybrid._arbitrate_digits = _arbitrate_recent_digits
    hybrid._recent_digit_bias_installed = True
