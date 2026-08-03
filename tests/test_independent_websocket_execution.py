from __future__ import annotations

import unittest

from enhanced_bot import independent_execution_outcome


class IndependentWebSocketExecutionTests(unittest.TestCase):
    def test_group_majority_sets_shared_execution_outcome(self) -> None:
        self.assertEqual(
            independent_execution_outcome(
                {
                    "DOT10000001": "win",
                    "DOT10000002": "loss",
                    "DOT10000003": "win",
                }
            ),
            "win",
        )

    def test_tied_group_does_not_change_shared_execution_outcome(self) -> None:
        self.assertIsNone(
            independent_execution_outcome(
                {
                    "DOT10000001": "win",
                    "DOT10000002": "loss",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
