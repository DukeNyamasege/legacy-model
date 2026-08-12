from __future__ import annotations

import asyncio
import pathlib
import unittest

import app.rotating_execution_cohorts as cohorts


ROOT = pathlib.Path(__file__).resolve().parents[1]


class _MemoryRepository:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def runtime_preference(self, key: str) -> str:
        return self.values.get(str(key), "")

    def set_runtime_preference(self, key: str, value: str) -> None:
        self.values[str(key)] = str(value)


class _QuietLogger:
    def warning(self, *_args, **_kwargs) -> None:
        return None

    def info(self, *_args, **_kwargs) -> None:
        return None


class _RoundRobinBot:
    def __init__(self) -> None:
        self.repository = _MemoryRepository()
        self.logger = _QuietLogger()


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

    def test_manual_proposals_do_not_mutate_the_active_cohort(self) -> None:
        source = (ROOT / "app" / "rotating_execution_cohorts.py").read_text()
        proposal = source.split(
            "async def _rotating_multi_proposal_for", 1
        )[1].split("async def _rotating_buy_selected_accounts", 1)[0]
        self.assertIn("async with _proposal_lock(bot)", proposal)
        self.assertIn("concurrent_proposals=false", proposal)
        self.assertNotIn("activate_cycle_accounts", proposal)
        self.assertNotIn("route.scope_ids = set(selected)", proposal)

    def test_manual_cohort_is_selected_only_at_purchase_boundary(self) -> None:
        source = (ROOT / "app" / "rotating_execution_cohorts.py").read_text()
        purchase = source.split(
            "async def _rotating_buy_selected_accounts", 1
        )[1].split("def install_rotating_execution_cohorts", 1)[0]
        self.assertIn("await _select_multi_route_cohort", purchase)
        self.assertIn("selection_at_purchase_boundary=true", purchase)
        self.assertIn("await activate_cycle_accounts", purchase)
        self.assertIn("finally:", purchase)
        self.assertIn("route.scope_ids = set(original_scope)", purchase)

    def test_system_rejects_stale_signals_before_cohort_activation(self) -> None:
        source = (
            ROOT / "app" / "scalable_group_execution_hardening.py"
        ).read_text()
        fresh_index = source.index("fresh = [")
        cohort_index = source.index("await cohorts.select_aidr_cycle")
        activation_index = source.index("await cohorts.activate_cycle_accounts")
        proposal_index = source.index("await cohorts.select_aidr_trigger")
        self.assertLess(fresh_index, cohort_index)
        self.assertLess(cohort_index, activation_index)
        self.assertLess(activation_index, proposal_index)
        self.assertIn("EXECUTION_COHORT_NOT_ACTIVATED", source)
        self.assertIn("stale_cycle_activation=false", source)

    def test_two_proposal_relays_are_nonfinancial(self) -> None:
        source = (ROOT / "app" / "proposal_relay_runtime.py").read_text()
        self.assertIn('DERIV_PROPOSAL_RELAY_COUNT", "2"', source)
        self.assertIn("PROPOSAL_RELAY_RECOVERED", source)
        self.assertIn("financial_requests=0", source)
        self.assertIn("direct_buy_parameters=true", source)
        self.assertNotIn('"buy":', source)

    def test_direct_financial_buy_uses_contract_parameters(self) -> None:
        source = (ROOT / "app" / "private_buy_parameter_hardening.py").read_text()
        self.assertIn('"buy": "1"', source)
        self.assertIn('"parameters": parameters', source)

    def test_custom_production_runtime_does_not_install_cohort_pressure_controls(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text()
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text()
        self.assertIn("python -m app.custom_strategy_worker", compose)
        self.assertIn("PRIVATE_WS_HANDSHAKE_CONCURRENCY:-2", compose)
        self.assertNotIn("EXECUTION_COHORT_SIZE", compose)
        self.assertNotIn("DERIV_PROPOSAL_RELAY_COUNT", compose)
        self.assertNotIn("PRIVATE_WS_BUY_CONCURRENCY", compose)
        self.assertNotIn("DERIV_WS_GROUP_CONCURRENCY", compose)
        self.assertNotIn("DERIV_WS_GROUP_SIZE", compose)
        self.assertNotIn("install_rotating_execution_cohorts", worker)
        self.assertNotIn("install_scalable_group_execution", worker)
        self.assertNotIn("install_guaranteed_signal_delivery", worker)

    def test_financial_transport_remains_independent_websocket_only(self) -> None:
        source = (ROOT / "app" / "rotating_execution_cohorts.py").read_text()
        self.assertIn("one_account_one_private_websocket=true", source)
        self.assertIn("bulk_purchase=false", source)
        self.assertIn("copy_trading=false", source)
        self.assertNotIn("bulk-purchase", source)


class RoundRobinFairnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_hundred_accounts_advance_ten_at_a_time(self) -> None:
        bot = _RoundRobinBot()
        accounts = set(range(1, 101))

        first = await cohorts._round_robin(
            bot,
            key="digits:over:NORMAL",
            managed_ids=accounts,
            count=10,
        )
        second = await cohorts._round_robin(
            bot,
            key="digits:over:NORMAL",
            managed_ids=accounts,
            count=10,
        )
        await asyncio.sleep(0.05)

        self.assertEqual(first, set(range(1, 11)))
        self.assertEqual(second, set(range(11, 21)))
        self.assertTrue(first.isdisjoint(second))


if __name__ == "__main__":
    unittest.main()
