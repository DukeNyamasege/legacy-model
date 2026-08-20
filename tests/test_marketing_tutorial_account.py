from __future__ import annotations

import pathlib
import unittest

from app import marketing_tutorial_account as marketing


ROOT = pathlib.Path(__file__).resolve().parents[1]


class MarketingTutorialAccountTests(unittest.TestCase):
    def test_exact_marketing_pair_and_partition_ratios(self) -> None:
        self.assertEqual(marketing.MARKETING_DOT_ACCOUNT_ID, "DOT93427967")
        self.assertEqual(marketing.MARKETING_ROT_ACCOUNT_ID, "ROT92069206")
        self.assertEqual(marketing.MARKETING_DOT_RATIO, 0.75)
        self.assertEqual(marketing.MARKETING_ROT_RATIO, 0.25)
        self.assertEqual(marketing._split_balance(10000.00), (7500.00, 2500.00))
        self.assertEqual(marketing._split_balance(10040.24), (7530.18, 2510.06))

    def test_metadata_is_explicitly_shared_demo_simulation(self) -> None:
        dot = marketing._marketing_metadata(view="dot")
        rot = marketing._marketing_metadata(view="rot")
        for payload in (dot, rot):
            self.assertTrue(payload["marketing_tutorial"])
            self.assertTrue(payload["simulation_only"])
            self.assertTrue(payload["demo_partition"])
            self.assertFalse(payload["real_money_execution"])
            self.assertEqual(payload["underlying_account_type"], "demo")
            self.assertEqual(payload["tutorial_execution_account_id"], "DOT93427967")
        self.assertEqual(dot["demo_partition_share"], 0.75)
        self.assertEqual(rot["demo_partition_share"], 0.25)

    def test_me_projection_exposes_independent_75_25_balances(self) -> None:
        base = {
            "authenticated": True,
            "managed_account_id": 11,
            "account_id_full": "DOT93427967",
            "account_type": "demo",
            "balance": 10000.00,
            "currency": "USD",
        }
        dot_row = type("Row", (), {"id": 11})()
        rot_row = type("Row", (), {"id": 22})()
        state = {
            "provider_balance": 10000.00,
            "dot_balance": 7500.00,
            "rot_balance": 2500.00,
            "currency": "USD",
            "contracts": {},
        }

        dot = marketing._project_me_payload(base, dot_row, rot_row, view="dot", state=state)
        rot = marketing._project_me_payload(base, dot_row, rot_row, view="rot", state=state)

        self.assertEqual(dot["managed_account_id"], 11)
        self.assertEqual(dot["account_id_full"], "DOT93427967")
        self.assertEqual(dot["account_type"], "demo")
        self.assertEqual(dot["balance"], 7500.00)
        self.assertEqual(dot["partition_provider_balance"], 10000.00)

        self.assertEqual(rot["managed_account_id"], 11)
        self.assertEqual(rot["presentation_managed_account_id"], 22)
        self.assertEqual(rot["account_id_full"], "ROT92069206")
        self.assertEqual(rot["login_id"], "ROT92069206")
        self.assertEqual(rot["account_type"], "real")
        self.assertEqual(rot["underlying_account_type"], "demo")
        self.assertEqual(rot["balance"], 2500.00)
        self.assertTrue(rot["simulation_only"])
        self.assertFalse(rot["real_money_execution"])

    def test_partition_receipts_freeze_owner_and_apply_full_profit_loss(self) -> None:
        source = (ROOT / "app" / "marketing_tutorial_account.py").read_text(encoding="utf-8")
        self.assertIn('if event == "OPEN"', source)
        self.assertIn('record.setdefault("view", owner)', source)
        self.assertIn('elif event == "SETTLED"', source)
        self.assertIn('delta = round(new_profit - (previous_profit if previous_profit is not None else 0.0), 8)', source)
        self.assertIn('balance_key = "rot_balance" if owner == "rot" else "dot_balance"', source)
        self.assertIn("_MAX_PARTITION_CONTRACTS", source)

    def test_reset_rebases_same_underlying_demo_to_75_25(self) -> None:
        source = (ROOT / "app" / "marketing_tutorial_account.py").read_text(encoding="utf-8")
        self.assertIn('previous_reset = _capture_endpoint(app, "/me/reset-demo-balance", "POST")', source)
        self.assertIn("DemoBalanceResetRequest(managed_account_id=int(dot_row.id))", source)
        self.assertIn("_reset_partition_state(", source)
        self.assertIn('"partition_dot_balance"', source)
        self.assertIn('"partition_rot_balance"', source)
        self.assertIn("Demo balance reset and re-split: 75% DOT / 25% ROT.", source)

    def test_backend_wraps_final_browser_direct_financial_controls(self) -> None:
        source = (ROOT / "app" / "marketing_tutorial_account.py").read_text(encoding="utf-8")
        for route in (
            '"/me/direct-execution/bootstrap"',
            '"/me/direct-execution/arm"',
            '"/me/direct-execution/receipt"',
        ):
            self.assertIn(route, source)
        self.assertIn("_ensure_dot_session(request, account, dot_row)", source)
        self.assertIn("Do not rewrite the bootstrap payload to ROT", source)
        self.assertIn("The financial session NEVER switches to ROT", source)
        self.assertIn("_apply_partition_receipt(", source)

    def test_selector_contains_only_dot_and_rot_partitions(self) -> None:
        source = (ROOT / "app" / "marketing_tutorial_account.py").read_text(encoding="utf-8")
        self.assertIn("Deliberately exactly two visible partitions", source)
        self.assertIn("_project_dot_account(dot_row, dot_payload", source)
        self.assertIn("_project_rot_account(dot_row, rot_row, rot_payload", source)
        self.assertIn('"scope": "marketing_shared_demo_partitions"', source)

    def test_marketing_install_runs_after_reset_and_browser_direct_authorities(self) -> None:
        source = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        reset_install = source.index("install_vps_demo_balance_reset(app)")
        cross = source.index("install_vps_cross_device_runtime_sync(app)")
        marketing_install = source.index("install_marketing_tutorial_account(app)")
        premium = source.index("install_premium_access_action6a(app)")
        self.assertLess(reset_install, marketing_install)
        self.assertLess(cross, marketing_install)
        self.assertLess(marketing_install, premium)

    def test_frontend_projects_full_partition_movement_and_guards_buy(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn("partitionLedgerBalance", source)
        self.assertIn("providerLedgerBalance", source)
        self.assertIn("partitionBaseline + (absolute - providerBaseline)", source)
        self.assertNotIn("absolute * ratio", source)
        self.assertNotIn("delta * ratio", source)
        self.assertIn("WebSocket.prototype.send = function guardedDemoPartitionSend", source)
        self.assertIn('Object.prototype.hasOwnProperty.call(payload, "buy")', source)
        self.assertIn("canSpend(price, account)", source)
        self.assertIn("demo partition insufficient", source)
        self.assertIn("deriv-real-flag", source)
        self.assertIn("DOT 75% · ROT 25%", source)
        self.assertIn("derivadmin:direct-balance-live", source)

    def test_frontend_asset_is_cache_busted(self) -> None:
        source = (ROOT / "scripts" / "inject-frontend-assets.mjs").read_text(encoding="utf-8")
        self.assertIn(
            '["direct-demo-reset-router-v1.js", "20260821-marketing-dot-rot-v3-partitions"]',
            source,
        )


if __name__ == "__main__":
    unittest.main()
