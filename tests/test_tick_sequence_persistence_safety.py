from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.tick_sequence_persistence_safety import (
    _persistable_tick_sequence,
    _wrap_signal_persistence,
)


ROOT = Path(__file__).resolve().parents[1]


class _LiveSequence:
    def __init__(self, value: int) -> None:
        self.current = int(value)

    def __int__(self) -> int:
        return int(self.current)


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

    def test_all_signal_persistence_boundaries_are_protected(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
