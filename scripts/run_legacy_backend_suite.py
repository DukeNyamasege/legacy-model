from __future__ import annotations

import unittest


def main() -> int:
    # The historical presentation-only assertions have already been removed from
    # these modules as part of the 6F-1 UI replacement. Run every test that remains
    # so backend/trading coverage is not reduced or selectively skipped.
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        ["test_rf_dir5", "test_strategy_logic"]
    )
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
