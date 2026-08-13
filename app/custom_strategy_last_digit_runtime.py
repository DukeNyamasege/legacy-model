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
        raise ValueError("Dynamic Matches/Differs signal is missing its qualifying last digit")
    resolved = dict(config)
    resolved["prediction"] = digit
    return resolved


def install_custom_strategy_last_digit_runtime() -> None:
    """Make exact-entry and Virtual Hook guards accept the resolved tick barrier."""

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
        # The canonical exact validator validates the concrete numeric barrier.
        # Temporarily give it the matching resolved fingerprint; the signal keeps
        # its stable dynamic-strategy fingerprint everywhere else.
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
