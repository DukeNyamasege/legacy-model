from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

from app.repositories.test2_repository import Test2Repository

REFERENCE_BASE_STAKE = 0.50
MINIMUM_PROFIT_RATIO = 0.01
SYSTEM_VIRTUAL_WINS_REQUIRED = 1

_INSTALLED = False
_ORIGINAL_SYSTEM_PERFORMANCE_SUMMARY = None


def _ceil_cents(value: float) -> float:
    return math.ceil(max(0.0, float(value)) * 100.0 - 1e-9) / 100.0


def _money(value: float) -> float:
    rounded = round(float(value or 0.0), 2)
    return 0.0 if rounded == -0.0 else rounded


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _period_trades(
    trades: list[dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
    include_virtual: bool,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for trade in trades:
        timestamp = _parse_timestamp(trade.get("signal_timestamp"))
        if timestamp is None or not (start <= timestamp < end):
            continue
        if trade.get("is_virtual") and not include_virtual:
            continue
        selected.append(trade)
    return sorted(
        selected,
        key=lambda item: (
            _parse_timestamp(item.get("signal_timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
            str(item.get("signal_id") or ""),
        ),
    )


def _profit_ratio(trade: dict[str, Any], outcome: str) -> float:
    reference = max(0.01, float(trade.get("reference_base_stake") or REFERENCE_BASE_STAKE))
    canonical_profit = float(trade.get("fixed_stake_profit") or 0.0)
    realized = canonical_profit / reference if reference > 0 else 0.0
    if outcome == "WIN" and realized > 0.0:
        return max(MINIMUM_PROFIT_RATIO, realized)
    try:
        expected = float(trade.get("expected_profit_ratio") or 0.90)
    except (TypeError, ValueError):
        expected = 0.90
    if not math.isfinite(expected):
        expected = 0.90
    return max(MINIMUM_PROFIT_RATIO, expected)


def _flat_trade_pnl(trade: dict[str, Any], stake: float, outcome: str) -> float:
    reference = max(0.01, float(trade.get("reference_base_stake") or REFERENCE_BASE_STAKE))
    canonical_profit = float(trade.get("fixed_stake_profit") or 0.0)
    if abs(canonical_profit) > 1e-12:
        return stake * (canonical_profit / reference)
    return stake * _profit_ratio(trade, outcome) if outcome == "WIN" else -stake


def _custom_martingale_stake(
    *,
    base_stake: float,
    consecutive_losses: int,
    trigger_losses: int,
    multiplier: float,
    max_levels: int = 10,
) -> tuple[float, int]:
    base = max(REFERENCE_BASE_STAKE, float(base_stake))
    losses = max(0, int(consecutive_losses))
    trigger = max(1, int(trigger_losses))
    if losses < trigger:
        return _ceil_cents(base), 0
    level = min(max(1, losses - trigger + 1), max(1, int(max_levels)))
    return _ceil_cents(base * (float(multiplier) ** level)), level


def _simulate_custom_profile(
    trades: list[dict[str, Any]],
    *,
    base_stake: float,
    trigger_losses: int,
    multiplier: float,
    label: str,
) -> dict[str, Any]:
    profit = 0.0
    peak = 0.0
    max_drawdown = 0.0
    total_staked = 0.0
    maximum_stake = base_stake
    consecutive_losses = 0
    max_level = 0
    for trade in trades:
        outcome = str(trade.get("outcome") or "").upper()
        if outcome not in {"WIN", "LOSS"}:
            continue
        stake, level = _custom_martingale_stake(
            base_stake=base_stake,
            consecutive_losses=consecutive_losses,
            trigger_losses=trigger_losses,
            multiplier=multiplier,
        )
        maximum_stake = max(maximum_stake, stake)
        max_level = max(max_level, level)
        total_staked += stake
        pnl = stake * _profit_ratio(trade, outcome) if outcome == "WIN" else -stake
        profit += pnl
        peak = max(peak, profit)
        max_drawdown = max(max_drawdown, peak - profit)
        consecutive_losses = 0 if outcome == "WIN" else consecutive_losses + 1
    return {
        "label": label,
        "trigger_losses": int(trigger_losses),
        "multiplier": round(float(multiplier), 2),
        "pnl": _money(profit),
        "maximum_stake": _money(maximum_stake),
        "maximum_level": int(max_level),
        "total_staked": _money(total_staked),
        "return_pct": round((profit / total_staked * 100.0), 2) if total_staked else 0.0,
        "max_drawdown": _money(max_drawdown),
    }


def _reference_summary(
    self: Test2Repository,
    *,
    start: datetime,
    end: datetime,
    simulated_base_stake: float = REFERENCE_BASE_STAKE,
    include_virtual: bool = False,
    trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    base_stake = _ceil_cents(min(1000.0, max(REFERENCE_BASE_STAKE, float(simulated_base_stake or REFERENCE_BASE_STAKE))))
    source_trades = trades if trades is not None else self.system_model_trades(
        start=start,
        end=end,
        include_virtual=include_virtual,
    )
    model_trades = _period_trades(
        [dict(trade) for trade in source_trades],
        start=start,
        end=end,
        include_virtual=include_virtual,
    )

    fixed_profit = 0.0
    system_profit = 0.0
    fixed_peak = 0.0
    system_peak = 0.0
    max_fixed_drawdown = 0.0
    max_system_drawdown = 0.0
    wins = 0
    losses = 0
    current_win_streak = 0
    current_loss_streak = 0
    longest_win_streak = 0
    longest_loss_streak = 0
    total_fixed_staked = 0.0
    total_system_staked = 0.0
    maximum_system_stake = base_stake
    monetary_system_trades = 0
    virtual_observations = 0

    mode = "NORMAL"
    recovery_debt = 0.0
    virtual_wins = 0

    for trade in model_trades:
        outcome = str(trade.get("outcome") or "").upper()
        if outcome not in {"WIN", "LOSS"}:
            continue

        if outcome == "WIN":
            wins += 1
            current_win_streak += 1
            current_loss_streak = 0
            longest_win_streak = max(longest_win_streak, current_win_streak)
        else:
            losses += 1
            current_loss_streak += 1
            current_win_streak = 0
            longest_loss_streak = max(longest_loss_streak, current_loss_streak)

        ratio = _profit_ratio(trade, outcome)
        flat_pnl = _flat_trade_pnl(trade, base_stake, outcome)
        fixed_profit += flat_pnl
        total_fixed_staked += base_stake
        fixed_peak = max(fixed_peak, fixed_profit)
        max_fixed_drawdown = max(max_fixed_drawdown, fixed_peak - fixed_profit)

        monetary = True
        if mode == "NORMAL":
            system_stake = base_stake
        elif mode == "RECOVERY":
            system_stake = max(base_stake, _ceil_cents(recovery_debt / ratio))
        else:
            system_stake = 0.0
            monetary = False

        if monetary:
            monetary_system_trades += 1
            maximum_system_stake = max(maximum_system_stake, system_stake)
            total_system_staked += system_stake
            system_pnl = system_stake * ratio if outcome == "WIN" else -system_stake
            system_profit += system_pnl
            system_peak = max(system_peak, system_profit)
            max_system_drawdown = max(max_system_drawdown, system_peak - system_profit)

            if mode == "NORMAL":
                if outcome == "LOSS":
                    recovery_debt = _money(recovery_debt + system_stake)
                    mode = "RECOVERY"
            elif mode == "RECOVERY":
                if outcome == "WIN":
                    recovery_debt = 0.0
                    virtual_wins = 0
                    mode = "NORMAL"
                else:
                    recovery_debt = _money(recovery_debt + system_stake)
                    virtual_wins = 0
                    mode = "VIRTUAL_WAIT"
        else:
            virtual_observations += 1
            if outcome == "WIN":
                virtual_wins += 1
                if virtual_wins >= SYSTEM_VIRTUAL_WINS_REQUIRED:
                    virtual_wins = 0
                    mode = "RECOVERY"
            else:
                virtual_wins = 0

    total = wins + losses
    version_material = "|".join(
        f"{trade.get('signal_id','')}:{trade.get('outcome','')}:"
        f"{float(trade.get('fixed_stake_profit') or 0.0):.8f}:"
        f"{trade.get('signal_timestamp','')}"
        for trade in model_trades
    )
    custom_profiles = [
        _simulate_custom_profile(model_trades, base_stake=base_stake, trigger_losses=1, multiplier=2.0, label="Custom x2 after 1 loss"),
        _simulate_custom_profile(model_trades, base_stake=base_stake, trigger_losses=2, multiplier=3.0, label="Custom x3 after 2 losses"),
        _simulate_custom_profile(model_trades, base_stake=base_stake, trigger_losses=3, multiplier=2.1, label="Custom x2.1 after 3 losses"),
    ]

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "simulated_base_stake": _money(base_stake),
        "reference_base_stake": REFERENCE_BASE_STAKE,
        "flat_stake": _money(base_stake),
        "stake_source": "standard_reference_base_not_user_contract_size",
        "maximum_martingale_stake": _money(maximum_system_stake),
        "model_data_version": hashlib.sha256(version_material.encode("utf-8")).hexdigest()[:16],
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / total * 100.0), 2) if total else 0.0,
        "fixed_pnl": _money(fixed_profit),
        "martingale_pnl": _money(system_profit),
        "without_martingale_pnl": _money(fixed_profit),
        "with_martingale_pnl": _money(system_profit),
        "max_drawdown_fixed": _money(max_fixed_drawdown),
        "max_drawdown_martingale": _money(max_system_drawdown),
        "current_drawdown_fixed": _money(fixed_peak - fixed_profit),
        "current_drawdown_martingale": _money(system_peak - system_profit),
        "longest_win_streak": longest_win_streak,
        "longest_loss_streak": longest_loss_streak,
        "current_loss_streak": current_loss_streak,
        "current_system_mode": mode,
        "recovery_debt_remaining": _money(recovery_debt),
        "system_monetary_trade_count": monetary_system_trades,
        "system_virtual_observation_count": virtual_observations,
        "total_fixed_staked": _money(total_fixed_staked),
        "total_martingale_staked": _money(total_system_staked),
        "fixed_return_pct": round((fixed_profit / total_fixed_staked * 100.0), 2) if total_fixed_staked else 0.0,
        "martingale_return_pct": round((system_profit / total_system_staked * 100.0), 2) if total_system_staked else 0.0,
        "current_drawdown_fixed_pct": round(((fixed_peak - fixed_profit) / total_fixed_staked * 100.0), 2) if total_fixed_staked else 0.0,
        "current_drawdown_martingale_pct": round(((system_peak - system_profit) / total_system_staked * 100.0), 2) if total_system_staked else 0.0,
        "max_drawdown_fixed_pct": round((max_fixed_drawdown / total_fixed_staked * 100.0), 2) if total_fixed_staked else 0.0,
        "max_drawdown_martingale_pct": round((max_system_drawdown / total_system_staked * 100.0), 2) if total_system_staked else 0.0,
        "custom_martingale_profiles": custom_profiles,
        "custom_profiles": custom_profiles,
        "global_reference_policy": {
            "base_stake": REFERENCE_BASE_STAKE,
            "stake_source": "Global Model P/L replays one standard $0.50 reference account. Personal user stakes never inflate public P/L or maximum stake.",
            "with_martingale": "System Martingale: OVER-2 loss arms one real PUT; failed real PUT enters virtual mode until two consecutive virtual PUT wins; next PUT is real.",
            "without_martingale": "Flat $0.50 reference stake on every model outcome.",
            "custom_profiles": "Read-only comparisons of common user Custom Martingale profiles on the same model sequence.",
        },
        "profit_accuracy_source": "canonical_model_reference_0_50_replay",
        "recovery_accounting_policy": "system_virtual_guard_reference_replay",
        "recovery_simulation_policy": "standard_reference_account_not_observed_user_stakes",
    }


def install_global_reference_dashboard() -> None:
    """Make public Global Bot Statistics independent of user stake sizes."""

    global _INSTALLED, _ORIGINAL_SYSTEM_PERFORMANCE_SUMMARY
    if _INSTALLED:
        return
    _ORIGINAL_SYSTEM_PERFORMANCE_SUMMARY = Test2Repository.system_performance_summary
    Test2Repository.system_performance_summary = _reference_summary
    Test2Repository._global_reference_dashboard_installed = True
    _INSTALLED = True
