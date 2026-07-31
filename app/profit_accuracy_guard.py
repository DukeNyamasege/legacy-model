from __future__ import annotations

from datetime import datetime
from typing import Any

from app.recovery import calculate_recovery_stake, ceil_cents
from app.repositories.test2_repository import Test2Repository

_INSTALLED = False


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _profit_ratio(row: dict[str, Any]) -> float:
    ratio = _float(row.get("expected_profit_ratio"), 0.0)
    if ratio > 0:
        return ratio
    stake = _float(row.get("actual_stake") or row.get("martingale_stake"), 0.0)
    profit = _float(row.get("actual_profit") or row.get("martingale_profit"), 0.0)
    if stake > 0 and profit > 0:
        return profit / stake
    fixed_profit = _float(row.get("fixed_stake_profit"), 0.0)
    base = _float(row.get("reference_base_stake"), 0.50) or 0.50
    if base > 0 and fixed_profit > 0:
        return fixed_profit / base
    return 0.0


def _actual_stake(row: dict[str, Any]) -> float:
    return round(
        _float(
            row.get("actual_stake")
            or row.get("buy_price")
            or row.get("martingale_stake")
            or row.get("reference_base_stake"),
            0.0,
        ),
        2,
    )


def _actual_profit(row: dict[str, Any]) -> float | None:
    for key in ("actual_profit", "profit", "martingale_profit"):
        if row.get(key) is not None:
            return round(_float(row.get(key), 0.0), 2)
    return None


def _ordered_real_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> str:
        return str(
            row.get("settlement_timestamp")
            or row.get("purchase_time")
            or row.get("signal_timestamp")
            or ""
        )

    return sorted(
        [
            row
            for row in rows
            if _upper(row.get("outcome")) in {"WIN", "LOSS"}
            and not bool(row.get("is_virtual", False))
        ],
        key=key,
    )


def _simulate_flat(rows: list[dict[str, Any]], base: float) -> tuple[float, float, float, float]:
    profit = 0.0
    peak = 0.0
    max_dd = 0.0
    current_dd = 0.0
    for row in rows:
        outcome = _upper(row.get("outcome"))
        ratio = _profit_ratio(row)
        pnl = round(base * ratio, 8) if outcome == "WIN" else -base
        profit += pnl
        peak = max(peak, profit)
        current_dd = peak - profit
        max_dd = max(max_dd, current_dd)
    return round(profit, 2), round(max_dd, 2), round(current_dd, 2), base


def _simulate_recovery(rows: list[dict[str, Any]], base: float) -> dict[str, float]:
    profit = 0.0
    peak = 0.0
    max_dd = 0.0
    current_dd = 0.0
    max_stake = base
    total_staked = 0.0
    debt = 0.0
    in_put_recovery = False

    for row in rows:
        outcome = _upper(row.get("outcome"))
        contract_type = _upper(row.get("contract_type"))
        ratio = _profit_ratio(row)
        is_put = contract_type == "PUT"
        stake = base
        if in_put_recovery or is_put:
            calculation = calculate_recovery_stake(
                base_stake=base,
                recovery_debt=debt,
                pre_trade_profit_ratio=ratio,
                minimum_stake=base,
            )
            stake = float(calculation.requested_stake)
        stake = ceil_cents(stake)
        pnl = round(stake * ratio, 8) if outcome == "WIN" else -stake
        profit += pnl
        total_staked += stake
        max_stake = max(max_stake, stake)

        # Debt is a single ledger.  Losses are added once.  The first winning PUT
        # clears the whole current cycle and the same loss is never recovered again.
        if is_put:
            if outcome == "WIN":
                debt = 0.0
                in_put_recovery = False
            else:
                debt = round(debt + abs(pnl), 2)
                in_put_recovery = True
        else:
            if outcome == "LOSS":
                debt = round(debt + abs(pnl), 2)
                in_put_recovery = True
            else:
                debt = 0.0
                in_put_recovery = False

        peak = max(peak, profit)
        current_dd = peak - profit
        max_dd = max(max_dd, current_dd)

    return {
        "profit": round(profit, 2),
        "max_drawdown": round(max_dd, 2),
        "current_drawdown": round(current_dd, 2),
        "maximum_stake": round(max_stake, 2),
        "total_staked": round(total_staked, 2),
        "remaining_recovery_debt": round(debt, 2),
    }


def _observed_actual(rows: list[dict[str, Any]], base: float) -> dict[str, float]:
    profit = 0.0
    peak = 0.0
    max_dd = 0.0
    current_dd = 0.0
    max_stake = base
    total_staked = 0.0
    for row in rows:
        stake = _actual_stake(row)
        actual = _actual_profit(row)
        if actual is None or stake <= 0:
            # Fall back to exact one-ledger simulation only when there is no
            # settled provider result available.
            return _simulate_recovery(rows, base)
        profit += actual
        total_staked += stake
        max_stake = max(max_stake, stake)
        peak = max(peak, profit)
        current_dd = peak - profit
        max_dd = max(max_dd, current_dd)
    return {
        "profit": round(profit, 2),
        "max_drawdown": round(max_dd, 2),
        "current_drawdown": round(current_dd, 2),
        "maximum_stake": round(max_stake, 2),
        "total_staked": round(total_staked, 2),
        "remaining_recovery_debt": _simulate_recovery(rows, base)["remaining_recovery_debt"],
    }


def _longest_streak(rows: list[dict[str, Any]], target: str) -> int:
    best = 0
    current = 0
    for row in rows:
        if _upper(row.get("outcome")) == target:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _wrap_system_performance_summary(original_summary):
    def wrapped(self: Test2Repository, **kwargs: Any) -> dict[str, Any]:
        result = original_summary(self, **kwargs)
        trades = kwargs.get("trades")
        if trades is None:
            trades = self.system_model_trades(
                start=kwargs["start"],
                end=kwargs["end"],
                include_virtual=False,
                viewer_managed_account_id=kwargs.get("viewer_managed_account_id"),
            )
        ordered = _ordered_real_trades(list(trades or []))
        base = ceil_cents(min(1000.0, max(0.50, _float(kwargs.get("simulated_base_stake"), 0.50))))

        flat_profit, flat_max_dd, flat_current_dd, flat_stake = _simulate_flat(ordered, base)
        observed = _observed_actual(ordered, base)
        simulated = _simulate_recovery(ordered, base)

        total = len(ordered)
        wins = sum(_upper(row.get("outcome")) == "WIN" for row in ordered)
        losses = total - wins
        result.update({
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total if total else 0.0,
            "longest_win_streak": _longest_streak(ordered, "WIN"),
            "longest_loss_streak": _longest_streak(ordered, "LOSS"),
            "fixed_pnl": flat_profit,
            "without_martingale_pnl": flat_profit,
            "flat_stake": flat_stake,
            "martingale_pnl": observed["profit"],
            "with_martingale_pnl": observed["profit"],
            "observed_martingale_pnl": observed["profit"],
            "simulated_martingale_pnl": simulated["profit"],
            "maximum_martingale_stake": observed["maximum_stake"],
            "observed_maximum_stake": observed["maximum_stake"],
            "simulated_maximum_martingale_stake": simulated["maximum_stake"],
            "max_drawdown_fixed": flat_max_dd,
            "current_drawdown_fixed": flat_current_dd,
            "max_drawdown_martingale": observed["max_drawdown"],
            "current_drawdown_martingale": observed["current_drawdown"],
            "simulated_max_drawdown_martingale": simulated["max_drawdown"],
            "simulated_current_drawdown_martingale": simulated["current_drawdown"],
            "recovery_debt_remaining": observed["remaining_recovery_debt"],
            "recovery_accounting_policy": "losses_added_once_first_winning_put_clears_cycle",
            "recovery_simulation_policy": "over2_loss_real_put_loss_virtual_until_two_put_wins",
            "profit_accuracy_source": "settled_actual_trades_preferred",
        })
        return result
    return wrapped


def install_profit_accuracy_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    Test2Repository.system_performance_summary = _wrap_system_performance_summary(
        Test2Repository.system_performance_summary
    )
    Test2Repository._profit_accuracy_guard_installed = True
    _INSTALLED = True
