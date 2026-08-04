from __future__ import annotations

import atexit
import os
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import BotState, Tick, utc_now
from app.repositories.test2_repository import Test2Repository

_INSTALLED = False
_STATES: "weakref.WeakKeyDictionary[Test2Repository, _BufferState]" = weakref.WeakKeyDictionary()
_STATES_LOCK = threading.RLock()


@dataclass
class _BufferState:
    rows: list[dict[str, Any]] = field(default_factory=list)
    last_flush_monotonic: float = field(default_factory=time.monotonic)
    latest_sequence: int = 0
    latest_connection_id: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)


def _batch_size() -> int:
    return max(10, min(1000, int(os.getenv("TICK_PERSIST_BATCH_SIZE", "50"))))


def _flush_seconds() -> float:
    return max(0.25, min(10.0, float(os.getenv("TICK_PERSIST_FLUSH_SECONDS", "1.0"))))


def _state(repository: Test2Repository) -> _BufferState:
    with _STATES_LOCK:
        value = _STATES.get(repository)
        if value is None:
            value = _BufferState()
            _STATES[repository] = value
        return value


def _insert_rows(repository: Test2Repository, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    dialect = repository.database.engine.dialect.name
    with repository.database.session() as session:
        if dialect == "postgresql":
            statement = postgres_insert(Tick).values(rows).on_conflict_do_nothing(
                index_elements=["run_id", "connection_session_id", "sequence_id"]
            )
            session.execute(statement)
        elif dialect == "sqlite":
            statement = sqlite_insert(Tick).values(rows).on_conflict_do_nothing(
                index_elements=["run_id", "connection_session_id", "sequence_id"]
            )
            session.execute(statement)
        else:
            session.execute(Tick.__table__.insert(), rows)


def flush_tick_buffer(
    repository: Test2Repository,
    *,
    force: bool = False,
) -> int:
    state = _state(repository)
    now = time.monotonic()
    with state.lock:
        if not state.rows:
            return 0
        if (
            not force
            and len(state.rows) < _batch_size()
            and now - state.last_flush_monotonic < _flush_seconds()
        ):
            return 0
        rows = state.rows
        state.rows = []
        latest_sequence = int(state.latest_sequence)
        latest_connection_id = str(state.latest_connection_id)
        state.last_flush_monotonic = now

    try:
        _insert_rows(repository, rows)
        with repository.database.session() as session:
            session.execute(
                update(BotState)
                .where(BotState.run_id == repository.run_id)
                .values(
                    current_sequence=latest_sequence,
                    current_connection_id=latest_connection_id,
                    last_heartbeat=utc_now(),
                )
            )
        return len(rows)
    except Exception:
        # Do not silently lose audit ticks if PostgreSQL temporarily rejects a
        # batch. Restore them in front of any newer rows and let the next tick or
        # graceful shutdown retry the same transaction.
        with state.lock:
            state.rows = rows + state.rows
            state.last_flush_monotonic = time.monotonic()
        raise


def _flush_all() -> None:
    with _STATES_LOCK:
        repositories = list(_STATES.keys())
    for repository in repositories:
        try:
            flush_tick_buffer(repository, force=True)
        except Exception:
            pass


def install_tick_persistence_buffer() -> None:
    """Persist ticks in bounded batches instead of one transaction per tick.

    Ten subscribed synthetic markets can generate many tick events each second.
    The former record_tick implementation committed every event and updated the
    same BotState row each time, producing continuous WAL/checkpoint pressure.
    Strategy analysis remains in memory; this buffer changes only persistence
    transport and flushes at least once per second or every configured batch.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_recent_digits = Test2Repository.recent_digits
    original_current_tick_sequence = Test2Repository.current_tick_sequence

    def buffered_record_tick(
        self: Test2Repository,
        *,
        sequence_id: int,
        symbol: str,
        epoch: int,
        tick_id: str,
        quote: float,
        final_digit: int,
        connection_session_id: str,
    ) -> None:
        state = _state(self)
        with state.lock:
            state.rows.append(
                {
                    "sequence_id": int(sequence_id),
                    "run_id": int(self.run_id),
                    "symbol": str(symbol),
                    "epoch": int(epoch),
                    "tick_id": str(tick_id),
                    "quote": float(quote),
                    "final_digit": int(final_digit),
                    "low_high_class": "LOW" if int(final_digit) <= 4 else "HIGH",
                    "received_timestamp": utc_now(),
                    "connection_session_id": str(connection_session_id),
                }
            )
            state.latest_sequence = int(sequence_id)
            state.latest_connection_id = str(connection_session_id)
        flush_tick_buffer(self)

    def recent_digits_after_flush(
        self: Test2Repository,
        limit: int = 6000,
        *,
        symbol: str | None = None,
    ) -> list[int]:
        flush_tick_buffer(self, force=True)
        return original_recent_digits(self, limit, symbol=symbol)

    def current_sequence_after_flush(
        self: Test2Repository,
        *,
        symbol: str | None = None,
    ) -> int:
        flush_tick_buffer(self, force=True)
        return original_current_tick_sequence(self, symbol=symbol)

    Test2Repository.record_tick = buffered_record_tick
    Test2Repository.recent_digits = recent_digits_after_flush
    Test2Repository.current_tick_sequence = current_sequence_after_flush
    Test2Repository.flush_tick_buffer = flush_tick_buffer  # type: ignore[attr-defined]
    Test2Repository._tick_persistence_buffer_installed = True
    atexit.register(_flush_all)
    _INSTALLED = True
