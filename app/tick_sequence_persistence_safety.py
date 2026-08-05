from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from app.repositories.rf_dir5_repository import RFDir5Repository
from app.repositories.test2_repository import Test2Repository


_INSTALLED = False
_VERSION = "persistable-live-tick-sequence-v1"


def _persistable_tick_sequence(value: Any) -> int:
    """Return the current live sequence as a plain PostgreSQL-safe integer."""

    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"Signal tick sequence is not persistable: {type(value).__name__}"
        ) from exc


def _wrap_signal_persistence(
    owner: type[Any],
    method_name: str,
) -> None:
    original = getattr(owner, method_name, None)
    if not callable(original):
        return
    if getattr(original, "_tick_sequence_persistence_safe", False):
        return

    @wraps(original)
    def persistence_safe(self: Any, signal: Any, *args: Any, **kwargs: Any) -> Any:
        if signal is None or not hasattr(signal, "tick_sequence"):
            return original(self, signal, *args, **kwargs)

        live_value = getattr(signal, "tick_sequence")
        stored_value = _persistable_tick_sequence(live_value)
        setattr(signal, "tick_sequence", stored_value)
        try:
            return original(self, signal, *args, **kwargs)
        finally:
            # The live delivery tracker must survive persistence so the purchase
            # boundary can still follow the latest tick during its short window.
            setattr(signal, "tick_sequence", live_value)

    persistence_safe._tick_sequence_persistence_safe = True  # type: ignore[attr-defined]
    persistence_safe._tick_sequence_persistence_version = _VERSION  # type: ignore[attr-defined]
    setattr(owner, method_name, persistence_safe)


def install_tick_sequence_persistence_safety() -> None:
    """Prevent temporary live-tick wrappers from reaching SQL integer columns."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Base digit candidate/proposal persistence.
    _wrap_signal_persistence(Test2Repository, "record_candidate")
    _wrap_signal_persistence(Test2Repository, "record_proposal")

    # Directional compatibility and shadow persistence can receive the same
    # signal after immediate-delivery wrappers have attached a live sequence.
    _wrap_signal_persistence(RFDir5Repository, "record_signal")
    _wrap_signal_persistence(RFDir5Repository, "create_shadow_contracts")

    RFDir5Repository._tick_sequence_persistence_safety_installed = True
    Test2Repository._tick_sequence_persistence_safety_installed = True
    _INSTALLED = True
