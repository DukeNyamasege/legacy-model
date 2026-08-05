from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.tick_sequence_persistence_safety import (
    _coerce_orm_live_sequences,
    _persistable_tick_sequence,
    _wrap_signal_persistence,
)


ROOT = Path(__file__).resolve().parents[1]


class _LiveSequence:
    def __init__(self, value: int) -> None:
        self.current = int(value)

    def __int__(self) -> int:
        return int(self.current)


class _Base(DeclarativeBase):
    pass


class _StoredSequence(_Base):
    __tablename__ = "test_stored_sequence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tick_sequence: Mapped[int] = mapped_column(Integer, nullable=False)


class TickSequencePersistenceSafetyTests(unittest.TestCase):
    def test_live_sequence_is_plain_integer_only_during_storage(self) -> None:
        observed: list[object] = []

        class Repository:
            def store(self, signal: object) -> int:
                observed.append(getattr(signal, "tick_sequence"))
                return int(getattr(signal, "tick_sequence"))

        _wrap_signal_persistence(Repository, "store")
        live = _LiveSequence(412)
        signal = SimpleNamespace(tick_sequence=live)

        result = Repository().store(signal)

        self.assertEqual(result, 412)
        self.assertEqual(observed, [412])
        self.assertIs(signal.tick_sequence, live)

    def test_orm_flush_guard_converts_direct_model_write(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        _Base.metadata.create_all(engine)

        Immediate = type(
            "_ImmediateTickSequence",
            (),
            {"__int__": lambda self: 733},
        )
        with Session(engine) as session:
            row = _StoredSequence(tick_sequence=Immediate())  # type: ignore[arg-type]
            session.add(row)
            _coerce_orm_live_sequences(session, None, None)
            self.assertEqual(row.tick_sequence, 733)
            session.commit()

        with Session(engine) as session:
            stored = session.get(_StoredSequence, 1)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.tick_sequence, 733)

    def test_invalid_sequence_fails_before_database_driver(self) -> None:
        with self.assertRaisesRegex(ValueError, "not persistable"):
            _persistable_tick_sequence(object())

    def test_final_worker_installer_activates_safety_after_execution_wrappers(self) -> None:
        source = (ROOT / "app" / "production_worker_integration.py").read_text(
            encoding="utf-8"
        )
        safety = source.index("install_tick_sequence_persistence_safety()")
        seamless = source.index("install_final_seamless_execution_runtime()")
        credential = source.index("install_bulk_credential_failure_hardening()")

        self.assertLess(seamless, safety)
        self.assertLess(safety, credential)

    def test_all_signal_and_orm_persistence_boundaries_are_protected(self) -> None:
        source = (ROOT / "app" / "tick_sequence_persistence_safety.py").read_text(
            encoding="utf-8"
        )
        for owner, method in (
            ("Test2Repository", "record_candidate"),
            ("Test2Repository", "record_proposal"),
            ("RFDir5Repository", "record_signal"),
            ("RFDir5Repository", "create_shadow_contracts"),
        ):
            self.assertIn(
                f'_wrap_signal_persistence({owner}, "{method}")',
                source,
            )
        self.assertIn("setattr(signal, \"tick_sequence\", live_value)", source)
        self.assertIn('event.listen(Session, "before_flush"', source)
        self.assertIn('type(value).__name__ != _LIVE_SEQUENCE_TYPE', source)


if __name__ == "__main__":
    unittest.main()
