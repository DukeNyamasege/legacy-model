from __future__ import annotations

from typing import Any

from app.repositories.test2_repository import Test2Repository

_INSTALLED = False
_ORIGINAL_SYSTEM_PERFORMANCE_SUMMARY = None


def _pick_number(payload: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            continue
    return round(float(default), 2)


def _apply_model_display_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose one consistent set of P/L keys for all dashboard renderers.

    The global reference replay intentionally reports public model performance as
    a standard $0.50 account, not observed user stakes. Older dashboard code still
    reads the simulated_* and observed_* aliases. Without these aliases the main
    Today's Model P/L card can fall back to +$0.00 and a default $1.00 stake even
    while the canonical $0.50 replay is available.
    """

    if not isinstance(payload, dict):
        return payload

    martingale = _pick_number(
        payload,
        "with_martingale_pnl",
        "martingale_pnl",
        "simulated_martingale_pnl",
        "observed_martingale_pnl",
    )
    fixed = _pick_number(
        payload,
        "without_martingale_pnl",
        "fixed_pnl",
        "simulated_fixed_pnl",
    )
    maximum_stake = _pick_number(
        payload,
        "maximum_martingale_stake",
        "simulated_maximum_martingale_stake",
        "observed_maximum_stake",
        "flat_stake",
        "simulated_base_stake",
        default=0.50,
    )
    flat_stake = _pick_number(
        payload,
        "flat_stake",
        "simulated_base_stake",
        "reference_base_stake",
        default=0.50,
    )

    payload.update(
        {
            "martingale_pnl": martingale,
            "with_martingale_pnl": martingale,
            "simulated_martingale_pnl": martingale,
            "observed_martingale_pnl": martingale,
            "fixed_pnl": fixed,
            "without_martingale_pnl": fixed,
            "simulated_fixed_pnl": fixed,
            "maximum_martingale_stake": maximum_stake,
            "simulated_maximum_martingale_stake": maximum_stake,
            "observed_maximum_stake": maximum_stake,
            "flat_stake": flat_stake,
            "simulated_base_stake": flat_stake,
            "reference_base_stake": 0.50,
            "profit_accuracy_source": payload.get("profit_accuracy_source")
            or "canonical_model_reference_0_50_replay",
        }
    )

    # Drawdown aliases used by the older renderer.
    for base_key, aliases in {
        "max_drawdown_martingale": (
            "simulated_max_drawdown_martingale",
            "observed_max_drawdown_martingale",
        ),
        "current_drawdown_martingale": (
            "simulated_current_drawdown_martingale",
            "observed_current_drawdown_martingale",
        ),
        "max_drawdown_martingale_pct": (
            "simulated_max_drawdown_martingale_pct",
            "observed_max_drawdown_martingale_pct",
        ),
        "current_drawdown_martingale_pct": (
            "simulated_current_drawdown_martingale_pct",
            "observed_current_drawdown_martingale_pct",
        ),
    }.items():
        selected = _pick_number(payload, base_key, *aliases)
        payload[base_key] = selected
        for alias in aliases:
            payload[alias] = selected

    return payload


def install_model_pnl_display_aliases() -> None:
    global _INSTALLED, _ORIGINAL_SYSTEM_PERFORMANCE_SUMMARY
    if _INSTALLED:
        return

    original = Test2Repository.system_performance_summary
    _ORIGINAL_SYSTEM_PERFORMANCE_SUMMARY = original

    def system_performance_summary_with_display_aliases(
        self: Test2Repository,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        return _apply_model_display_aliases(payload)

    Test2Repository.system_performance_summary = system_performance_summary_with_display_aliases
    Test2Repository._model_pnl_display_aliases_installed = True
    _INSTALLED = True
