from __future__ import annotations

from typing import Any

from app import custom_strategy_settlement as settlement
from app import exact_strategy_execution_authority as exact
from app import custom_strategy_v1 as custom


_INSTALLED = False


def _dynamic(config: Any) -> bool:
    if not isinstance(config, dict):
        return False
    trade_type = str(config.get("trade_type") or "").strip().lower()
    return trade_type in {"matches", "differs"} and config.get("prediction") is None


def _resolved_config(config: dict[str, Any], signal: Any) -> dict[str, Any]:
    if not _dynamic(config):
        return config
    digit = int(getattr(signal, "signal_last_digit", -1))
    if not 0 <= digit <= 9:
        raise ValueError("Dynamic Matches/Differs signal is missing its qualifying prediction digit")

    # Freeze the dynamic prediction into a concrete numeric contract for every
    # downstream validator. Merely setting prediction=<digit> is insufficient:
    # normalize_custom_strategy() will prefer prediction_mode from reanalyze and
    # convert the config back to a dynamic sentinel. That caused latched signals
    # such as DIFFERS 4 to be compared against the literal barrier "last_digit",
    # repeatedly triggering runtime resynchronization instead of a BUY.
    resolved = dict(config)
    resolved["prediction"] = digit
    resolved.pop("prediction_mode", None)
    resolved.pop("prediction_window", None)
    reanalyze = dict(resolved.get("reanalyze") or {})
    reanalyze.pop("prediction_mode", None)
    reanalyze.pop("prediction_window", None)
    resolved["reanalyze"] = reanalyze
    return resolved


def install_custom_strategy_last_digit_runtime() -> None:
    """Make entry and Virtual Hook guards accept the frozen dynamic barrier."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_virtual_match = settlement.virtual_signal_matches_config
    original_exact_assert = exact._assert_strategy_exact

    def virtual_signal_matches_config(config: dict[str, Any], signal: Any) -> bool:
        try:
            return original_virtual_match(_resolved_config(config, signal), signal)
        except (TypeError, ValueError):
            return False

    def assert_strategy_exact(item: Any, signal: Any) -> None:
        config = getattr(item, "config", {})
        if not _dynamic(config):
            original_exact_assert(item, signal)
            return

        resolved = _resolved_config(config, signal)
        original_config = item.config
        original_trigger = str(getattr(signal, "trigger_name", "") or "")
        # Validate the concrete numeric contract created when the signal qualified.
        # The stable dynamic strategy fingerprint is restored immediately after the
        # check so persistence/routing still identifies the user's saved strategy.
        item.config = resolved
        signal.trigger_name = (
            f"CUSTOM-V2-{custom.custom_strategy_fingerprint(resolved)[:8].upper()}"
        )
        try:
            original_exact_assert(item, signal)
        finally:
            item.config = original_config
            signal.trigger_name = original_trigger

    settlement.virtual_signal_matches_config = virtual_signal_matches_config
    exact._assert_strategy_exact = assert_strategy_exact
    _INSTALLED = True
