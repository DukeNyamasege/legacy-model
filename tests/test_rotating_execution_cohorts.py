from __future__ import annotations

import pathlib
import unittest

import app.rotating_execution_cohorts as cohorts


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RotatingExecutionCohortTests(unittest.TestCase):
    def test_default_cohort_is_ten_accounts(self) -> None:
        self.assertEqual(cohorts.COHORT_SIZE, 10)

    def test_normal_accounts_keep_reserved_capacity_during_recovery(self) -> None:
        allocation = cohorts._allocate_counts(
            capacity=10,
            normal_count=100,
            recovery_counts={
                "recovery": 100,
                "post_virtual": 100,
                "virtual": 100,
            },
        )
        self.assertEqual(sum(allocation.values()), 10)
        self.assertEqual(allocation["normal"], 2)
        self.assertEqual(
            allocation["recovery"]
            + allocation["post_virtual"]
            + allocation["virtual"],
            8,
        )

    def test_normal_pool_uses_whole_cohort_without_recovery(self) -> None:
        allocation = cohorts._allocate_counts(
            capacity=10,
            normal_count=100,
            recovery_counts={
                "recovery": 0,
                "post_virtual": 0,
                "virtual": 0,
            },
        )
        self.assertEqual(allocation["normal"], 10)
        self.assertEqual(sum(allocation.values()), 10)

    def test_small_population_is_never_duplicated(self) -> None:
        allocation = cohorts._allocate_counts(
            capacity=10,
            normal_count=3,
            recovery_counts={
                "recovery": 2,
                "post_virtual": 1,
                "virtual": 1,
            },
        )
        self.assertEqual(sum(allocation.values()), 7)
        self.assertLessEqual(allocation["normal"], 3)
        self.assertLessEqual(allocation["recovery"], 2)
        self.assertLessEqual(allocation["post_virtual"], 1)
        self.assertLessEqual(allocation["virtual"], 1)

    def test_private_reconnect_eligibility_is_cohort_scoped(self) -> None:
        source = (ROOT / "app" / "rotating_execution_cohorts.py").read_text()
        self.assertIn("private_ws._still_configured = _cohort_still_configured", source)
        self.assertIn("pending_contracts", source)
        self.assertIn("_rotating_active_managed_ids", source)
        self.assertIn("all_other_accounts_standby=true", source)

    def test_provider_trigger_proposals_are_sequential(self) -> None:
        source = (ROOT / "app" / "rotating_execution_cohorts.py").read_text()
        self.assertIn("AIDR_TRIGGER_PROPOSAL_ATTEMPT", source)
        self.assertIn("concurrent_proposals=false", source)
        trigger = source.split("async def select_aidr_trigger", 1)[1].split(
            "async def _select_multi_route_cohort", 1
        )[0]
        self.assertNotIn("asyncio.gather", trigger)

    def test_all_strategies_use_rotation_before_proposal(self) -> None:
        source = (ROOT / "app" / "rotating_execution_cohorts.py").read_text()
        self.assertIn("multi._proposal_for = _rotating_multi_proposal_for", source)
        self.assertIn("route.scope_ids = set(selected)", source)
        self.assertIn("recovery_state_preserved=true", source)

    def test_system_hardening_selects_cohort_before_provider_work(self) -> None:
        source = (
            ROOT / "app" / "scalable_group_execution_hardening.py"
        ).read_text()
        cohort_index = source.index("await cohorts.select_aidr_cycle")
        proposal_index = source.index("await cohorts.select_aidr_trigger")
        self.assertLess(cohort_index, proposal_index)
        self.assertIn("await cohorts.activate_cycle_accounts", source)
        self.assertIn("all_accounts_same_signal=false", source)

    def test_financial_transport_remains_independent_websocket_only(self) -> None:
        source = (ROOT / "app" / "rotating_execution_cohorts.py").read_text()
        self.assertIn("one_account_one_private_websocket=true", source)
        self.assertIn("bulk_purchase=false", source)
        self.assertIn("copy_trading=false", source)
        self.assertNotIn("bulk-purchase", source)


if __name__ == "__main__":
    unittest.main()
