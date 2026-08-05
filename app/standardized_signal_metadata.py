from __future__ import annotations

import time
from typing import Any

from app.hybrid_digit_put import DigitSignal
from app.strategy.rise_fall_strategy import SignalEvent


_INSTALLED = False
_METADATA_TTL_SECONDS = 120.0
_CYCLE_METADATA: dict[tuple[type[Any], str], tuple[str, float]] = {}


def _key(value: Any) -> tuple[type[Any], str]:
    return type(value), str(getattr(value, "signal_id", "") or "")


def _prune() -> None:
    cutoff = time.monotonic() - _METADATA_TTL_SECONDS
    stale = [key for key, (_cycle_id, created) in _CYCLE_METADATA.items() if created < cutoff]
    for key in stale:
        _CYCLE_METADATA.pop(key, None)


def standardized_cycle_id(value: Any) -> str:
    _prune()
    payload = _CYCLE_METADATA.get(_key(value))
    return str(payload[0]) if payload is not None else ""


def clear_standardized_cycle_id(value: Any) -> None:
    _CYCLE_METADATA.pop(_key(value), None)


def _install_for(signal_type: type[Any]) -> None:
    if getattr(signal_type, "_standardized_signal_metadata_installed", False):
        return

    original_setattr = signal_type.__setattr__
    original_getattr = getattr(signal_type, "__getattr__", None)

    def metadata_setattr(self: Any, name: str, value: Any) -> None:
        if name == "_standardized_cycle_id":
            signal_id = str(getattr(self, "signal_id", "") or "")
            if signal_id:
                _prune()
                _CYCLE_METADATA[(type(self), signal_id)] = (
                    str(value or ""),
                    time.monotonic(),
                )
            return
        original_setattr(self, name, value)

    def metadata_getattr(self: Any, name: str) -> Any:
        if name == "_standardized_cycle_id":
            return standardized_cycle_id(self)
        if callable(original_getattr):
            return original_getattr(self, name)
        raise AttributeError(name)

    signal_type.__setattr__ = metadata_setattr
    signal_type.__getattr__ = metadata_getattr
    signal_type._standardized_signal_metadata_installed = True


def install_signal_metadata_accessors() -> None:
    """Install only transient cycle-ID accessors on slotted signal classes.

    This narrow installer is safe for unit tests and utility processes because it
    does not change account routing, candidate persistence, proposal handling or
    financial execution. Production startup calls the full installer below.
    """

    _install_for(DigitSignal)
    _install_for(SignalEvent)


def install_standardized_signal_metadata() -> None:
    """Install metadata accessors and activate the production shared signal clock."""

    global _INSTALLED
    if _INSTALLED:
        return

    install_signal_metadata_accessors()

    # Install after Strategy V2 has resolved persisted selections but before the
    # standardized and guaranteed-delivery wrappers capture AIDR purchase hooks.
    # This removes the independent per-tick manual candidate generator and makes
    # the System AIDR gate the sole entry clock for every selectable contract.
    from app.shared_system_strategy_clock import install_shared_system_strategy_clock

    install_shared_system_strategy_clock()
    _INSTALLED = True
