from __future__ import annotations

from functools import wraps
from typing import Any

from sqlalchemy import Integer, event, inspect
from sqlalchemy.orm import Session

from app.repositories.rf_dir5_repository import RFDir5Repository
from app.repositories.test2_repository import Test2Repository


_INSTALLED = False
_EVENT_INSTALLED = False
_VERSION = "persistable-live-tick-sequence-v2"
_LIVE_SEQUENCE_TYPE = "_ImmediateTickSequence"


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
            # The live delivery tracker must survive repository persistence so the
            # purchase boundary can still follow the latest tick during its short
            # preparation window.
            setattr(signal, "tick_sequence", live_value)

    persistence_safe._tick_sequence_persistence_safe = True  # type: ignore[attr-defined]
    persistence_safe._tick_sequence_persistence_version = _VERSION  # type: ignore[attr-defined]
    setattr(owner, method_name, persistence_safe)


def _coerce_orm_live_sequences(
    session: Session,
    _flush_context: Any,
    _instances: Any,
) -> None:
    """Convert live sequence wrappers on mapped integer columns before SQL bind."""

    for instance in set(session.new).union(session.dirty):
        try:
            mapper = inspect(instance).mapper
        except Exception:
            continue
        for attribute in mapper.column_attrs:
            columns = list(attribute.columns)
            if not columns or not isinstance(columns[0].type, Integer):
                continue
            key = str(attribute.key)
            try:
                value = getattr(instance, key)
            except Exception:
                continue
            if type(value).__name__ != _LIVE_SEQUENCE_TYPE:
                continue
            setattr(instance, key, _persistable_tick_sequence(value))


def _install_orm_flush_guard() -> None:
    global _EVENT_INSTALLED
    if _EVENT_INSTALLED:
        return
    if not event.contains(Session, "before_flush", _coerce_orm_live_sequences):
        event.listen(Session, "before_flush", _coerce_orm_live_sequences)
    _EVENT_INSTALLED = True


def install_tick_sequence_persistence_safety() -> None:
    """Prevent temporary live-tick wrappers from reaching SQL integer columns."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Protect known signal persistence helpers while preserving the in-memory live
    # tracker after each helper returns.
    _wrap_signal_persistence(Test2Repository, "record_candidate")
    _wrap_signal_persistence(Test2Repository, "record_proposal")
    _wrap_signal_persistence(RFDir5Repository, "record_signal")
    _wrap_signal_persistence(RFDir5Repository, "create_shadow_contracts")

    # Final fail-safe for direct ORM model writes outside repository helpers.
    _install_orm_flush_guard()

    RFDir5Repository._tick_sequence_persistence_safety_installed = True
    Test2Repository._tick_sequence_persistence_safety_installed = True
    _INSTALLED = True
