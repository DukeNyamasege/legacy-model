from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.aidr_virtual_soft_gate import (
    POST_VIRTUAL_ALIGNMENT,
    POST_VIRTUAL_MINIMUM_LIVE_EDGE,
    POST_VIRTUAL_TIGHTENING_FACTOR,
    soft_alignment_for_barrier,
    soft_edge_for_signal,
)


class AIDRVirtualSoftGateTests(unittest.TestCase):
    def test_over4_uses_ordinary_probability_without_tightening(self) -> None:
        self.assertAlmostEqual(POST_VIRTUAL_TIGHTENING_FACTOR, 1.0)
        self.assertAlmostEqual(POST_VIRTUAL_ALIGNMENT, 0.50)
        self.assertAlmostEqual(soft_alignment_for_barrier(4, 0.60), 0.50)

    def test_real_over4_recovery_keeps_small_positive_live_edge(self) -> None:
        signal = SimpleNamespace(barrier="4")
        self.assertAlmostEqual(POST_VIRTUAL_MINIMUM_LIVE_EDGE, 0.005)
        self.assertAlmostEqual(soft_edge_for_signal(signal, 0.015), 0.005)

    def test_normal_and_first_recovery_gates_are_unchanged(self) -> None:
        self.assertAlmostEqual(soft_alignment_for_barrier(1, 0.60), 0.60)
        self.assertAlmostEqual(soft_alignment_for_barrier(3, 0.60), 0.60)
        self.assertAlmostEqual(
            soft_edge_for_signal(SimpleNamespace(barrier="1"), 0.015),
            0.015,
        )
        self.assertAlmostEqual(
            soft_edge_for_signal(SimpleNamespace(barrier="3"), 0.015),
            0.015,
        )

    def test_soft_gate_never_tightens_a_lower_operator_threshold(self) -> None:
        self.assertAlmostEqual(soft_alignment_for_barrier(4, 0.45), 0.45)
        self.assertAlmostEqual(
            soft_edge_for_signal(SimpleNamespace(barrier="4"), 0.003),
            0.003,
        )


if __name__ == "__main__":
    unittest.main()
