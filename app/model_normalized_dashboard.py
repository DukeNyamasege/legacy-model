from __future__ import annotations

from typing import Any

from app.profit_accuracy_guard import (
    _float,
    _longest_streak,
    _ordered_real_trades,
    _simulate_flat,
    _simulate_recovery,
    _upper,
)
from app.recovery import ceil_cents
from app.repositories.test2_repository import Test2Repository

_INSTALLED = False


def _wrap_model_normalized_summary(original_summary):
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

        # Global model cards must not be polluted by personal trader stake sizes.
        # The default and canonical reference stake is 0.50 USD. The system
        # performance simulator may still pass a different simulated_base_stake;
        # otherwise every global dashboard card remains model-normalized at $0.50.
        base = ceil_cents(
            min(
                1000.0,
                max(0.50, _float(kwargs.get("simulated_base_stake"), 0.50)),
            )
        )
        flat_profit, flat_max_dd, flat_current_dd, flat_stake = _simulate_flat(
            ordered,
            base,
        )
        system = _simulate_recovery(ordered, base)

        total = len(ordered)
        wins = sum(_upper(row.get("outcome")) == "WIN" for row in ordered)
        losses = total - wins

        result.update(
            {
                "total_trades": total,
                "wins": wins,
                "losses": losses,
                "win_rate": wins / total if total else 0.0,
                "longest_win_streak": _longest_streak(ordered, "WIN"),
                "longest_loss_streak": _longest_streak(ordered, "LOSS"),
                "reference_base_stake": base,
                "global_reference_stake": base,
                "fixed_pnl": flat_profit,
                "without_martingale_pnl": flat_profit,
                "flat_stake": flat_stake,
                "martingale_pnl": system["profit"],
                "with_martingale_pnl": system["profit"],
                "simulated_martingale_pnl": system["profit"],
                "maximum_martingale_stake": system["maximum_stake"],
                "simulated_maximum_martingale_stake": system["maximum_stake"],
                "max_drawdown_fixed": flat_max_dd,
                "current_drawdown_fixed": flat_current_dd,
                "max_drawdown_martingale": system["max_drawdown"],
                "current_drawdown_martingale": system["current_drawdown"],
                "simulated_max_drawdown_martingale": system["max_drawdown"],
                "simulated_current_drawdown_martingale": system["current_drawdown"],
                "recovery_debt_remaining": system["remaining_recovery_debt"],
                "profit_accuracy_source": "model_normalized_reference_stake",
                "global_profit_policy": (
                    "model_outcomes_simulated_at_reference_stake_not_user_stakes"
                ),
                "recovery_accounting_policy": (
                    "losses_added_once_first_winning_put_clears_cycle"
                ),
                "recovery_simulation_policy": (
                    "system_martingale_exact_debt_recovery_at_reference_stake"
                ),
            }
        )
        return result

    return wrapped


def install_model_normalized_dashboard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    Test2Repository.system_performance_summary = _wrap_model_normalized_summary(
        Test2Repository.system_performance_summary
    )
    Test2Repository._model_normalized_dashboard_installed = True
    _INSTALLED = True
