from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

import app.hybrid_digit_put as hybrid
import app.hybrid_runtime_config as runtime
from app.models import AccountRiskState, utc_now
from app.recovery import ceil_cents
from app.repositories.rf_dir5_repository import (
    NORMAL_MODE,
    REAL_RECOVERY_PENDING,
    RFDir5Repository,
    StakePlan,
    VIRTUAL_MODE,
    VIRTUAL_WAITING_FOR_WIN,
)
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy.over2_strategy import TEST2_SYMBOLS
from app.aidr_strategy_contract import AIDR_STRATEGY_CONTRACT


_PRODUCT = AIDR_STRATEGY_CONTRACT["product"]
_EXECUTION = AIDR_STRATEGY_CONTRACT["execution"]
_QUALITY = AIDR_STRATEGY_CONTRACT["quality"]

AIDR_VERSION = str(_PRODUCT["version"])
AIDR_TRIGGER_BASE = "AIDR-O1-V1"
AIDR_TRIGGER_RECOVERY = "AIDR-O3-V1"
AIDR_TRIGGER_POST_VIRTUAL = "AIDR-O4-V2"
AIDR_RUN_ID = str(_PRODUCT["run_id"])
AIDR_STATE_KEY = "aidr_over1_over3_v1:state"
AIDR_ACCOUNT_EPOCH_PREFIX = "aidr_over1_over3_v1:account_epoch:"
AIDR_SPLIT_PREFIX = "aidr_split_remaining:"
NORMAL_BARRIER = int(_EXECUTION["normal_barrier"])
RECOVERY_BARRIER = int(_EXECUTION["first_recovery_barrier"])
POST_VIRTUAL_BARRIER = int(_EXECUTION["post_virtual_recovery_barrier"])
RECENT_WINDOW = int(_QUALITY["recent_window"])
MID_WINDOW = int(_QUALITY["mid_window"])
LONG_WINDOW = int(_QUALITY["long_window"])
DEEP_WINDOW = int(_QUALITY["deep_window"])
MIN_BASE_HIT_RATE = float(_QUALITY["minimum_normal_hit_rate"])
MIN_RECOVERY_HIT_RATE = float(_QUALITY["minimum_recovery_hit_rate"])
MIN_LIVE_EDGE = float(_QUALITY["minimum_live_edge"])
VIRTUAL_WINS_REQUIRED = int(_EXECUTION["virtual_confirmation_wins"])
RECOVERY_PROFIT_BUFFER = float(_QUALITY["recovery_profit_buffer"])

_INSTALLED = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def calculate_full_recovery_stake(
    *,
    base_stake: float,
    recovery_debt: float,
    proposal_profit_ratio: float,
) -> float:
    """Size one contract to cover all debt plus a one-cent rounding buffer."""

    base = ceil_cents(max(0.35, float(base_stake or 0.0)))
    debt = max(0.0, float(recovery_debt or 0.0))
    ratio = max(0.0001, float(proposal_profit_ratio or 0.0))
    # Match the shared recovery calculator: AIDR recovery wins must reset only
    # after covering provider/markup payout variation, not just the ideal quote.
    target_profit = ceil_cents(
        debt + max(float(RECOVERY_PROFIT_BUFFER), 0.05, debt * 0.06)
    )
    return ceil_cents(max(base, target_profit / ratio))


def remaining_recovery_debt(*, recovery_debt: float, recovered_profit: float) -> float:
    """Keep any amount that the settled recovery profit did not actually repay."""

    return max(
        0.0,
        round(
            max(0.0, float(recovery_debt or 0.0))
            - max(0.0, float(recovered_profit or 0.0)),
            2,
        ),
    )


def _split_key(managed_account_id: int) -> str:
    return f"{AIDR_SPLIT_PREFIX}{int(managed_account_id)}"


def _read_split_remaining(repo: Any, managed_account_id: int) -> int:
    """Return the post-virtual full-recovery marker.

    Older deployments stored ``2`` here for two split targets. Treat every
    positive legacy value as one pending full-debt recovery so upgrades do not
    discard an account's existing recovery debt.
    """
    try:
        raw = str(repo.runtime_preference(_split_key(managed_account_id)) or "").strip()
        return 1 if int(raw or "0") > 0 else 0
    except Exception:
        return 0


def _write_split_remaining(repo: Any, managed_account_id: int, value: int) -> None:
    try:
        repo.set_runtime_preference(_split_key(managed_account_id), "1" if int(value) > 0 else "0")
    except Exception:
        pass


def _clear_split_remaining(repo: Any, managed_account_id: int) -> None:
    _write_split_remaining(repo, managed_account_id, 0)


def _enabled_accounts(bot: RFDir5TradingBot) -> list[tuple[str, str, int]]:
    original = getattr(bot, "_aidr_original_eligible_accounts", None)
    if callable(original):
        pairs = original()
    else:
        pairs = bot._eligible_purchase_accounts()
    result: list[tuple[str, str, int]] = []
    for token, account_id in pairs:
        managed_id = bot._managed_account_id_for_token(token)
        if managed_id is None:
            continue
        result.append((token, account_id, int(managed_id)))
    return result


def _risk_states(bot: RFDir5TradingBot, managed_ids: set[int]) -> dict[int, AccountRiskState]:
    if not managed_ids:
        return {}
    with bot.repository.database.session() as session:
        rows = session.scalars(
            select(AccountRiskState).where(AccountRiskState.managed_account_id.in_(sorted(managed_ids)))
        ).all()
    return {int(row.managed_account_id): row for row in rows}


def _account_groups(bot: RFDir5TradingBot) -> tuple[set[int], set[int], set[int]]:
    normal, initial_recovery, post_virtual_recovery, virtual = _account_recovery_groups(bot)
    return normal, initial_recovery | post_virtual_recovery, virtual


def _account_recovery_groups(
    bot: RFDir5TradingBot,
) -> tuple[set[int], set[int], set[int], set[int]]:
    accounts = _enabled_accounts(bot)
    ids = {managed_id for _token, _account, managed_id in accounts}
    states = _risk_states(bot, ids)
    normal: set[int] = set()
    initial_recovery: set[int] = set()
    post_virtual_recovery: set[int] = set()
    virtual: set[int] = set()
    for _token, _account, managed_id in accounts:
        state = states.get(managed_id)
        if state is None or (
            float(state.recovery_loss_debt or 0.0) <= 0.009
            and not state.recovery_pending
            and not state.recovery_attempt_active
            and state.protection_mode not in {VIRTUAL_WAITING_FOR_WIN, REAL_RECOVERY_PENDING}
        ):
            normal.add(managed_id)
            continue
        if state.protection_mode == VIRTUAL_WAITING_FOR_WIN:
            virtual.add(managed_id)
        elif _read_split_remaining(bot.repository, managed_id) > 0:
            post_virtual_recovery.add(managed_id)
        else:
            initial_recovery.add(managed_id)
    return normal, initial_recovery, post_virtual_recovery, virtual


def _probability(digits: list[int], *, window: int, barrier: int) -> float:
    sample = [int(value) for value in digits[-window:] if 0 <= int(value) <= 9]
    if not sample:
        return 0.0
    return sum(digit > barrier for digit in sample) / len(sample)


def _entropy_score(digits: list[int], *, window: int = 100) -> float:
    sample = [int(value) for value in digits[-window:] if 0 <= int(value) <= 9]
    if not sample:
        return 0.0
    counts = [sample.count(digit) for digit in range(10)]
    entropy = 0.0
    for count in counts:
        if count <= 0:
            continue
        p = count / len(sample)
        entropy -= p * math.log(p, 2)
    return max(0.0, min(1.0, entropy / math.log(10, 2)))


def _digit_metrics(digits: list[int], *, barrier: int) -> dict[str, float]:
    p20 = _probability(digits, window=RECENT_WINDOW, barrier=barrier)
    p50 = _probability(digits, window=MID_WINDOW, barrier=barrier)
    p100 = _probability(digits, window=LONG_WINDOW, barrier=barrier)
    p500 = _probability(digits, window=DEEP_WINDOW, barrier=barrier)
    entropy = _entropy_score(digits)
    weighted = 0.42 * p20 + 0.25 * p50 + 0.20 * p100 + 0.13 * p500
    # A simple AI-inspired regime score: probability alignment plus entropy sanity.
    # It avoids trading only from one tiny recent spike while still preferring
    # strong high-digit pressure for OVER contracts.
    alignment = min(p20, p50, p100)
    regime_score = max(0.0, min(1.0, 0.72 * weighted + 0.18 * alignment + 0.10 * entropy))
    return {
        "p20": p20,
        "p50": p50,
        "p100": p100,
        "p500": p500,
        "entropy": entropy,
        "weighted": weighted,
        "alignment": alignment,
        "regime_score": regime_score,
    }


def _make_aidr_candidate(
    bot: RFDir5TradingBot,
    symbol: str,
    tick: dict[str, Any],
    *,
    barrier: int = NORMAL_BARRIER,
    recovery: bool = False,
) -> hybrid.DigitSignal | None:
    market = bot.market_states[symbol]
    digits = [int(value) for value in market.raw_tick_digits if 0 <= int(value) <= 9]
    if len(digits) < DEEP_WINDOW:
        return None
    metrics = _digit_metrics(digits, barrier=barrier)
    minimum_rate = MIN_RECOVERY_HIT_RATE if recovery else MIN_BASE_HIT_RATE
    if min(metrics["p20"], metrics["p50"], metrics["p100"]) + 1e-12 < minimum_rate:
        bot.logger.info(
            "AIDR_DIGIT_PREFILTER role=%s symbol=%s type=DIGITOVER barrier=%s "
            "p20=%.5f p50=%.5f p100=%.5f p500=%.5f required=%.5f entropy=%.5f",
            "RECOVERY" if recovery else "NORMAL",
            symbol,
            barrier,
            metrics["p20"],
            metrics["p50"],
            metrics["p100"],
            metrics["p500"],
            minimum_rate,
            metrics["entropy"],
        )
        return None

    quote = Decimal(str(tick["quote"]))
    epoch = int(tick.get("epoch") or 0)
    tick_id = bot._tick_identity(symbol, epoch, quote)
    trigger_digits = tuple(digits[-RECENT_WINDOW:])
    trigger_name = (
        AIDR_TRIGGER_POST_VIRTUAL
        if barrier == POST_VIRTUAL_BARRIER
        else AIDR_TRIGGER_RECOVERY
        if recovery
        else AIDR_TRIGGER_BASE
    )
    return hybrid.DigitSignal(
        signal_id=str(uuid.uuid4()),
        run_id=AIDR_RUN_ID,
        strategy_version=AIDR_VERSION,
        symbol=symbol,
        direction=f"OVER_{barrier}",
        contract_type="DIGITOVER",
        duration_ticks=1,
        reference_entry_quote=quote,
        quality_score=max(1, min(10, int(round(metrics["regime_score"] * 10)))),
        signal_tick_epoch=epoch,
        signal_tick_id=tick_id,
        generated_at=_now_iso(),
        generated_monotonic=time.monotonic(),
        connection_session_id=bot.connection_session_id,
        tick_sequence=int(market.tick_sequence),
        barrier=str(barrier),
        trigger_name=trigger_name,
        trigger_digits=trigger_digits,
        signal_last_digit=trigger_digits[-1],
        p100=metrics["p20"],
        p500=metrics["p100"],
        p1000=metrics["p500"],
        lower95=metrics["alignment"],
        weighted_probability=metrics["weighted"],
    )


def _clone_recovery_candidate(candidate: hybrid.DigitSignal) -> hybrid.DigitSignal:
    return replace(
        candidate,
        signal_id=str(uuid.uuid4()),
        direction=f"OVER_{RECOVERY_BARRIER}",
        barrier=str(RECOVERY_BARRIER),
        trigger_name=AIDR_TRIGGER_RECOVERY,
    )


async def _proposal_ok(bot: RFDir5TradingBot, signal: hybrid.DigitSignal, minimum_edge: float) -> tuple[hybrid.DigitSignal, Any] | None:
    signal, economics = await hybrid._digit_proposal(bot, signal)
    if economics is None:
        bot.repository.mark_signal(signal.signal_id, status="SKIP_UNPROFITABLE_QUOTE")
        return None
    break_even = float(economics.break_even_probability)
    live_edge = float(signal.weighted_probability) - break_even
    signal.proposal_ask_price = float(economics.stake)
    signal.proposal_payout = float(economics.payout)
    signal.break_even_probability = break_even
    signal.validated_edge = live_edge
    if live_edge + 1e-12 < minimum_edge:
        bot.repository.mark_signal(signal.signal_id, status="SKIP_AIDR_DIGIT_EDGE")
        bot.logger.info(
            "AIDR_DIGIT_SKIP signal_id=%s symbol=%s barrier=%s weighted=%.5f "
            "break_even=%.5f edge=%.5f minimum_edge=%.5f",
            signal.signal_id,
            signal.symbol,
            signal.barrier,
            float(signal.weighted_probability),
            break_even,
            live_edge,
            minimum_edge,
        )
        return None
    return signal, economics


async def _buy_for_scope(
    bot: RFDir5TradingBot,
    signal: hybrid.DigitSignal,
    economics: Any,
    managed_ids: set[int],
    *,
    recovery_enabled: bool,
) -> None:
    if not managed_ids:
        bot.repository.mark_signal(signal.signal_id, status="SKIP_NO_SCOPE_ACCOUNTS")
        return
    original_recovery_enabled = bool(bot.risk_config.recovery_enabled)
    previous_scope = getattr(bot, "_aidr_purchase_scope_ids", None)
    try:
        bot._aidr_purchase_scope_ids = set(managed_ids)
        bot.risk_config.recovery_enabled = bool(recovery_enabled)
        await bot._buy_selected_accounts(signal, economics)
    finally:
        bot.risk_config.recovery_enabled = original_recovery_enabled
        bot._aidr_purchase_scope_ids = previous_scope


async def _arbitrate_aidr_digits(bot: RFDir5TradingBot) -> None:
    cfg = bot.test2_config.hybrid_strategy
    await asyncio.sleep(float(getattr(cfg, "candidate_window_ms", 75)) / 1000.0)
    queued = list(bot.hybrid_digit_candidates.values())
    bot.hybrid_digit_candidates.clear()
    if not queued:
        return

    bot._prune_stale_pending_contracts("aidr_digit_pre_proposal")
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

    normal_ids, real_recovery_ids, virtual_ids = _account_groups(bot)
    recovery_ids = real_recovery_ids | virtual_ids
    tasks: list[Any] = []
    role_by_signal: dict[str, str] = {}

    if normal_ids:
        for candidate in fresh:
            role_by_signal[candidate.signal_id] = "NORMAL"
            tasks.append(_proposal_ok(bot, candidate, MIN_LIVE_EDGE))
    if recovery_ids:
        for candidate in fresh:
            recovery_candidate = _clone_recovery_candidate(candidate)
            bot.repository.record_candidate(recovery_candidate)
            role_by_signal[recovery_candidate.signal_id] = "RECOVERY"
            tasks.append(_proposal_ok(bot, recovery_candidate, MIN_LIVE_EDGE))

    if not tasks:
        for candidate in fresh:
            bot.repository.mark_signal(candidate.signal_id, status="SKIP_NO_ENABLED_ACCOUNTS")
        return

    results = await asyncio.gather(*tasks, return_exceptions=True)
    qualified: dict[str, list[tuple[float, hybrid.DigitSignal, Any]]] = {"NORMAL": [], "RECOVERY": []}
    for result in results:
        if isinstance(result, Exception) or result is None:
            continue
        signal, economics = result
        role = role_by_signal.get(signal.signal_id, "NORMAL")
        if bot.market_states[signal.symbol].tick_sequence != signal.tick_sequence:
            bot.repository.mark_signal(signal.signal_id, status="SKIP_STALE_SIGNAL", stale=True)
            continue
        score = float(signal.validated_edge or 0.0) + 0.05 * float(signal.lower95 or 0.0)
        qualified[role].append((score, signal, economics))

    for role, scope_ids, recovery_enabled in (
        ("NORMAL", normal_ids, False),
        ("RECOVERY", recovery_ids, True),
    ):
        group = qualified.get(role) or []
        if not group:
            continue
        group.sort(key=lambda item: (-item[0], -float(item[1].weighted_probability), item[1].symbol))
        score, selected, economics = group[0]
        for _score, other, _economics in group[1:]:
            bot.repository.mark_signal(other.signal_id, status="SKIP_MARKET_ARBITRATION")
        bot.logger.warning(
            "AIDR_DIGIT_SELECTED role=%s signal_id=%s symbol=%s type=%s barrier=%s "
            "accounts=%s weighted=%.5f break_even=%.5f edge=%.5f score=%.5f",
            role,
            selected.signal_id,
            selected.symbol,
            selected.contract_type,
            selected.barrier,
            len(scope_ids),
            float(selected.weighted_probability),
            float(selected.break_even_probability or 0.0),
            float(selected.validated_edge or 0.0),
            score,
        )
        await _buy_for_scope(bot, selected, economics, scope_ids, recovery_enabled=recovery_enabled)


def _reset_virtual_counters(state: AccountRiskState) -> None:
    state.protection_mode = NORMAL_MODE
    state.entered_virtual_mode_at = None
    state.virtual_observation_count = 0
    state.virtual_win_count = 0
    state.virtual_loss_count = 0
    state.current_virtual_loss_streak = 0


def _enter_virtual_mode(state: AccountRiskState) -> None:
    state.protection_mode = VIRTUAL_WAITING_FOR_WIN
    state.recovery_pending = True
    state.recovery_attempt_active = False
    state.entered_virtual_mode_at = utc_now()
    state.virtual_observation_count = 0
    state.virtual_win_count = 0
    state.virtual_loss_count = 0
    state.current_virtual_loss_streak = 0
    if state.recovery_pending_since is None:
        state.recovery_pending_since = utc_now()


def _mode_label(state: AccountRiskState | None) -> str:
    if state is None:
        return NORMAL_MODE
    if state.protection_mode == VIRTUAL_WAITING_FOR_WIN:
        return VIRTUAL_MODE
    if state.protection_mode == REAL_RECOVERY_PENDING:
        return "RECOVERY_PENDING"
    return NORMAL_MODE


def _record_account_outcome_aidr(
    self: RFDir5Repository,
    *,
    managed_account_id: int,
    account_id_masked: str = "",
    profit: float,
    current_balance: float,
    recovery_enabled: bool = True,
    recovery_trigger_losses: int = 1,
    virtual_protection_enabled: bool = True,
    virtual_trigger_actual_losses: int = 2,
) -> dict[str, Any]:
    del recovery_trigger_losses, virtual_protection_enabled, virtual_trigger_actual_losses
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
                protection_mode=NORMAL_MODE,
            )
            session.add(state)
        elif account_id_masked and state.account_id_masked != account_id_masked:
            state.account_id_masked = str(account_id_masked)

        if state.trading_day != today:
            state.trading_day = today
            state.daily_start_balance = max(0.0, float(current_balance) - float(profit))
            state.session_profit = 0.0
            state.consecutive_losses = 0
            state.recovery_loss_debt = 0.0
            state.recovery_pending = False
            state.recovery_attempt_active = False
            state.recovery_pending_since = None
            _reset_virtual_counters(state)
            _clear_split_remaining(self.base, int(managed_account_id))

        previous_mode = state.protection_mode
        was_recovery = bool(state.recovery_attempt_active or state.protection_mode == REAL_RECOVERY_PENDING)
        state.session_profit += float(profit)
        state.recovery_attempt_active = False

        if float(profit) <= 0:
            loss_amount = round(abs(float(profit)), 2)
            state.consecutive_losses = int(state.consecutive_losses or 0) + 1
            state.recovery_loss_debt = round(float(state.recovery_loss_debt or 0.0) + loss_amount, 2)
            state.recovery_pending = bool(recovery_enabled and state.recovery_loss_debt > 0.009)
            if state.recovery_pending:
                if was_recovery:
                    _clear_split_remaining(self.base, int(managed_account_id))
                    _enter_virtual_mode(state)
                else:
                    state.protection_mode = REAL_RECOVERY_PENDING
                    if state.recovery_pending_since is None:
                        state.recovery_pending_since = utc_now()
            else:
                _reset_virtual_counters(state)
                _clear_split_remaining(self.base, int(managed_account_id))
        else:
            # Both recovery contracts target all recorded debt in one win. Only
            # clear what the settled provider profit actually repaid; retaining
            # a rounding or repricing residual prevents false recovery reports.
            residual_debt = (
                remaining_recovery_debt(
                    recovery_debt=float(state.recovery_loss_debt or 0.0),
                    recovered_profit=float(profit),
                )
                if was_recovery
                else 0.0
            )
            state.recovery_loss_debt = residual_debt
            state.recovery_pending = bool(recovery_enabled and residual_debt > 0.009)
            state.consecutive_losses = 0
            state.recovery_attempt_active = False
            if state.recovery_pending:
                _clear_split_remaining(self.base, int(managed_account_id))
                _enter_virtual_mode(state)
            else:
                state.recovery_pending_since = None
                _reset_virtual_counters(state)
                _clear_split_remaining(self.base, int(managed_account_id))

        state.equity_high_water = max(float(state.equity_high_water or 0.0), float(current_balance))
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
            "protection_mode": _mode_label(state),
            "raw_protection_state": state.protection_mode,
            "protection_state_changed": previous_mode != state.protection_mode,
            "recovery_policy": "aidr_over1_over3_over4_full_v2",
            "split_recovery_remaining": _read_split_remaining(self.base, int(managed_account_id)),
        }


def _plan_stake_aidr(original_plan_stake):
    def wrapped(
        self: RFDir5Repository,
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
        plan = original_plan_stake(
            self,
            managed_account_id=managed_account_id,
            account_id_masked=account_id_masked,
            current_balance=current_balance,
            requested_stake=requested_stake,
            proposal_profit_ratio=proposal_profit_ratio,
            recovery_enabled=recovery_enabled,
            recovery_trigger_losses=1,
            minimum_stake=minimum_stake,
            virtual_protection_enabled=virtual_protection_enabled,
            maximum_recovery_balance_fraction=maximum_recovery_balance_fraction,
            minimum_balance_reserve=minimum_balance_reserve,
        )
        if plan.stake is None:
            return plan
        with self.database.session() as session:
            state = session.get(AccountRiskState, int(managed_account_id))
            debt = float(state.recovery_loss_debt or 0.0) if state is not None else 0.0
            recovery_pending = bool(state is not None and state.recovery_pending and debt > 0.009)
            virtual = bool(state is not None and state.protection_mode == VIRTUAL_WAITING_FOR_WIN)
        if virtual:
            return StakePlan(None, "virtual OVER-4 confirmation active", is_recovery=True, recovery_debt=debt)
        if not recovery_pending or not recovery_enabled:
            return plan
        split_remaining = _read_split_remaining(self.base, int(managed_account_id))
        base = ceil_cents(max(float(minimum_stake), float(requested_stake)))
        stake = calculate_full_recovery_stake(
            base_stake=base,
            recovery_debt=debt,
            proposal_profit_ratio=proposal_profit_ratio,
        )
        return StakePlan(
            stake=stake,
            reason=(
                "AIDR OVER-4 full-debt recovery"
                if split_remaining > 0
                else "AIDR OVER-3 exact recovery"
            ),
            is_recovery=True,
            recovery_debt=debt,
            required_recovery_stake=stake,
        )
    return wrapped


def _settle_virtual_aidr(original_settle):
    def wrapped(self: RFDir5Repository, **kwargs: Any) -> list[dict[str, Any]]:
        settled = original_settle(self, **{**kwargs, "exit_after_wins": VIRTUAL_WINS_REQUIRED})
        for item in settled:
            protection = item.get("protection") or {}
            account_masked = str(item.get("account") or "")
            if not account_masked:
                continue
            with self.database.session() as session:
                state = session.scalar(
                    select(AccountRiskState).where(AccountRiskState.account_id_masked == account_masked)
                )
                managed_id = int(state.managed_account_id) if state is not None else None
                mode = state.protection_mode if state is not None else NORMAL_MODE
                wins = int(state.virtual_win_count or 0) if state is not None else 0
            if managed_id is None:
                continue
            if mode == REAL_RECOVERY_PENDING or str(protection.get("mode") or "") == "RECOVERY_PENDING":
                _write_split_remaining(self.base, managed_id, 1)
                self.base.set_managed_account_execution_status(
                    managed_id,
                    "recovery_pending",
                    "One virtual OVER-4 win confirmed recovery. Next real OVER-4 recovery targets the full debt once.",
                )
            elif mode == VIRTUAL_WAITING_FOR_WIN:
                self.base.set_managed_account_execution_status(
                    managed_id,
                    "virtual_protection",
                    f"Virtual OVER-4 confirmation active: consecutive wins {wins}/{VIRTUAL_WINS_REQUIRED}.",
                )
        return settled
    return wrapped


def _scoped_eligible(original_eligible):
    def wrapped(self: RFDir5TradingBot) -> list[tuple[str, str]]:
        accounts = list(original_eligible(self))
        scope = getattr(self, "_aidr_purchase_scope_ids", None)
        if not scope:
            return accounts
        scope_ids = {int(value) for value in scope}
        return [
            (token, account_id)
            for token, account_id in accounts
            if (self._managed_account_id_for_token(token) in scope_ids)
        ]
    return wrapped


def install_ai_digit_recovery_v1_strategy() -> None:
    """Install OVER-1 normal, OVER-3 first recovery and OVER-4 full recovery."""
    global _INSTALLED
    if _INSTALLED:
        return

    hybrid.HYBRID_STATE_KEY = AIDR_STATE_KEY
    hybrid.ACCOUNT_EPOCH_PREFIX = AIDR_ACCOUNT_EPOCH_PREFIX
    hybrid.PRIMARY_DIGITS = "AIDR_NORMAL_OVER1"
    hybrid.PUT_RECOVERY = "AIDR_RECOVERY_OVER3_OVER4"

    runtime.HYBRID_RUNTIME_CONFIG = replace(
        runtime.HYBRID_RUNTIME_CONFIG,
        version=AIDR_VERSION,
        primary_markets=tuple(TEST2_SYMBOLS),
        recovery_markets=tuple(TEST2_SYMBOLS),
        primary_contract_type="DIGITOVER",
        primary_barrier=NORMAL_BARRIER,
        over_barrier=NORMAL_BARRIER,
        under_barrier=8,
        recent_window=RECENT_WINDOW,
        minimum_recent_hit_rate=MIN_BASE_HIT_RATE,
        minimum_live_edge=MIN_LIVE_EDGE,
    )

    hybrid._make_digit_candidate = lambda bot, symbol, tick: _make_aidr_candidate(
        bot,
        symbol,
        tick,
        barrier=NORMAL_BARRIER,
        recovery=False,
    )
    hybrid._arbitrate_digits = _arbitrate_aidr_digits

    original_eligible = RFDir5TradingBot._eligible_purchase_accounts
    RFDir5TradingBot._eligible_purchase_accounts = _scoped_eligible(original_eligible)

    original_init = RFDir5TradingBot.__init__

    def aidr_init(self: RFDir5TradingBot, config_path: str | None = None) -> None:
        self._aidr_original_eligible_accounts = lambda: original_eligible(self)
        original_init(self, config_path)
        self.test2_config.model.run_id = AIDR_RUN_ID
        self.contract_type = "DIGITOVER"
        self.contract_barrier = str(NORMAL_BARRIER)
        self.logger.warning(
            "AIDR_OVER1_OVER3_OVER4_ACTIVE version=%s normal=DIGITOVER_%s first_recovery=DIGITOVER_%s "
            "virtual_and_full_recovery=DIGITOVER_%s virtual_wins_required=%s full_debt_once=true put_removed=true",
            AIDR_VERSION,
            NORMAL_BARRIER,
            RECOVERY_BARRIER,
            POST_VIRTUAL_BARRIER,
            VIRTUAL_WINS_REQUIRED,
        )

    RFDir5TradingBot.__init__ = aidr_init

    def no_put_schedule(self: RFDir5TradingBot) -> None:
        if getattr(self, "rf_candidate_queue", None):
            self.rf_candidate_queue.clear()
        return None

    async def no_put_arbitrate(self: RFDir5TradingBot) -> None:
        if getattr(self, "rf_candidate_queue", None):
            self.rf_candidate_queue.clear()
        return None

    RFDir5TradingBot._schedule_candidate_arbitration = no_put_schedule
    RFDir5TradingBot._arbitrate_candidates = no_put_arbitrate

    RFDir5Repository.record_account_outcome = _record_account_outcome_aidr
    RFDir5Repository.plan_stake = _plan_stake_aidr(RFDir5Repository.plan_stake)
    RFDir5Repository.settle_due_virtual_trades = _settle_virtual_aidr(RFDir5Repository.settle_due_virtual_trades)

    RFDir5TradingBot._aidr_over1_over3_installed = True
    _INSTALLED = True
