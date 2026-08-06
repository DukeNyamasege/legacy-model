from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.final_multi_strategy_execution import _groups_from_snapshot
from app.shared_system_strategy_clock import _contract_spec
from app.strategy_v2_preferences import normalize_strategy


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "final_multi_strategy_execution.py"
PRODUCTION = ROOT / "app" / "production_worker_integration.py"


class FinalMultiStrategyExecutionTests(unittest.TestCase):
    def test_all_selectable_contracts_have_exact_routes(self) -> None:
        self.assertEqual(
            _contract_spec(normalize_strategy("digits", "over", 2)),
            ("DIGITOVER", "OVER_2", "2"),
        )
        self.assertEqual(
            _contract_spec(normalize_strategy("digits", "under", 7)),
            ("DIGITUNDER", "UNDER_7", "7"),
        )
        self.assertEqual(
            _contract_spec(normalize_strategy("parity", "even")),
            ("DIGITEVEN", "EVEN", ""),
        )
        self.assertEqual(
            _contract_spec(normalize_strategy("parity", "odd")),
            ("DIGITODD", "ODD", ""),
        )
        self.assertEqual(
            _contract_spec(normalize_strategy("direction", "rise")),
            ("CALL", "RISE", ""),
        )
        self.assertEqual(
            _contract_spec(normalize_strategy("direction", "fall")),
            ("PUT", "FALL", ""),
        )

    def test_one_snapshot_routes_system_and_every_manual_family(self) -> None:
        routes = [
            SimpleNamespace(
                managed_id=1,
                selection=normalize_strategy("system", "system"),
                mode="NORMAL_MODE",
                split_remaining=0,
            ),
            SimpleNamespace(
                managed_id=2,
                selection=normalize_strategy("digits", "over", 4),
                mode="NORMAL_MODE",
                split_remaining=0,
            ),
            SimpleNamespace(
                managed_id=3,
                selection=normalize_strategy("digits", "under", 6),
                mode="RECOVERY_PENDING",
                split_remaining=0,
            ),
            SimpleNamespace(
                managed_id=4,
                selection=normalize_strategy("parity", "even"),
                mode="NORMAL_MODE",
                split_remaining=0,
            ),
            SimpleNamespace(
                managed_id=5,
                selection=normalize_strategy("direction", "rise"),
                mode="VIRTUAL_MODE",
                split_remaining=0,
            ),
        ]
        system, manual, unknown = _groups_from_snapshot(
            routes,
            {1, 2, 3, 4, 5, 99},
            source_role="NORMAL",
        )
        self.assertEqual(system, {1})
        self.assertEqual(unknown, {99})
        routed = {
            (
                group[0].family,
                group[0].side,
                group[0].prediction,
                group[2],
            ): group[1]
            for group in manual
        }
        self.assertEqual(routed[("digits", "over", 4, "NORMAL")], {2})
        self.assertEqual(routed[("digits", "under", 6, "NORMAL")], {3})
        self.assertEqual(routed[("parity", "even", None, "NORMAL")], {4})
        self.assertEqual(routed[("direction", "rise", None, "NORMAL")], {5})

    def test_system_dispatch_does_not_wait_for_manual_proposals(self) -> None:
        source = MODULE.read_text(encoding="utf-8")
        create_task = source.index("proposal_task = asyncio.create_task(")
        system_dispatch = source.index('"SYSTEM_PURCHASE_DISPATCH_IMMEDIATE')
        system_buy = source.index("await shared._exact_scope_buy(", system_dispatch)
        proposal_await = source.index("economics = await proposal_task")
        self.assertLess(create_task, system_dispatch)
        self.assertLess(system_dispatch, system_buy)
        self.assertLess(system_buy, proposal_await)
        self.assertIn("system_waits_for_manual=false", source)
        self.assertIn("other_strategy_groups_continue=true", source)
        self.assertIn("single_strategy_snapshot=true", source)

    def test_final_installer_is_after_shared_and_seamless_wrappers(self) -> None:
        source = PRODUCTION.read_text(encoding="utf-8")
        self.assertIn("install_final_multi_strategy_execution", source)
        self.assertLess(
            source.index("install_final_shared_system_strategy_clock()"),
            source.index("install_final_multi_strategy_execution()"),
        )
        self.assertLess(
            source.index("install_final_seamless_execution_runtime()"),
            source.index("install_final_multi_strategy_execution()"),
        )


if __name__ == "__main__":
    unittest.main()
