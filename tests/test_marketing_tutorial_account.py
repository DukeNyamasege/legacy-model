from __future__ import annotations

import pathlib
import unittest
from unittest.mock import patch

from app import marketing_tutorial_account as marketing


ROOT = pathlib.Path(__file__).resolve().parents[1]


class MarketingTutorialAccountTests(unittest.TestCase):
    def test_exact_marketing_pair_and_ratio(self) -> None:
        self.assertEqual(marketing.MARKETING_DOT_ACCOUNT_ID, "DOT93427967")
        self.assertEqual(marketing.MARKETING_ROT_ACCOUNT_ID, "ROT92069206")
        self.assertEqual(marketing.MARKETING_ROT_RATIO, 0.25)

    def test_metadata_is_explicitly_simulation_only(self) -> None:
        payload = marketing._marketing_metadata(view="rot")
        self.assertTrue(payload["marketing_tutorial"])
        self.assertTrue(payload["simulation_only"])
        self.assertFalse(payload["real_money_execution"])
        self.assertEqual(payload["tutorial_execution_account_id"], "DOT93427967")
        self.assertEqual(payload["tutorial_display_account_id"], "ROT92069206")
        self.assertEqual(payload["tutorial_balance_ratio"], 0.25)

    def test_rot_me_projection_keeps_dot_execution_managed_id(self) -> None:
        base = {
            "authenticated": True,
            "managed_account_id": 11,
            "account_id_full": "DOT93427967",
            "account_type": "demo",
            "balance": 10040.24,
            "currency": "USD",
        }
        dot_row = type("Row", (), {"id": 11})()
        rot_row = type("Row", (), {"id": 22})()
        with patch.object(marketing, "_rot_balance", return_value=2510.06):
            projected = marketing._project_me_payload(
                base,
                dot_row,
                rot_row,
                view="rot",
            )
        self.assertEqual(projected["managed_account_id"], 11)
        self.assertEqual(projected["presentation_managed_account_id"], 22)
        self.assertEqual(projected["account_id_full"], "ROT92069206")
        self.assertEqual(projected["login_id"], "ROT92069206")
        self.assertEqual(projected["account_type"], "real")
        self.assertEqual(projected["balance"], 2510.06)
        self.assertTrue(projected["simulation_only"])
        self.assertFalse(projected["real_money_execution"])

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
        self.assertIn("financial control path stay bound to DOT93427967", source)

    def test_selector_hides_raw_real_account_and_keeps_only_dot_plus_rot(self) -> None:
        source = (ROOT / "app" / "marketing_tutorial_account.py").read_text(encoding="utf-8")
        self.assertIn("Deliberately exactly two visible accounts", source)
        self.assertIn("No underlying/extra real", source)
        self.assertIn("_project_dot_account(dot_row, dot_payload", source)
        self.assertIn("_project_rot_account(dot_row, rot_row, rot_payload", source)

    def test_marketing_install_runs_after_browser_direct_transport(self) -> None:
        source = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        cross = source.index("install_vps_cross_device_runtime_sync(app)")
        marketing_install = source.index("install_marketing_tutorial_account(app)")
        premium = source.index("install_premium_access_action6a(app)")
        self.assertLess(cross, marketing_install)
        self.assertLess(marketing_install, premium)

    def test_frontend_projects_provider_balance_without_changing_execution(self) -> None:
        source = (ROOT / "dashboard" / "direct-demo-reset-router-v1.js").read_text(encoding="utf-8")
        self.assertIn("presentationRatio", source)
        self.assertIn("absolute * ratio", source)
        self.assertIn("delta * ratio", source)
        self.assertIn('delete detail.loginid', source)
        self.assertIn("deriv-real-flag", source)
        self.assertIn("expectedId", source)
        self.assertIn("Tutorial</span><b>Demo execution", source)
        self.assertIn("ROT view · linked DOT demo", source)
        self.assertIn("derivadmin:direct-balance-live", source)

    def test_frontend_asset_is_cache_busted(self) -> None:
        source = (ROOT / "scripts" / "inject-frontend-assets.mjs").read_text(encoding="utf-8")
        self.assertIn(
            '["direct-demo-reset-router-v1.js", "20260821-marketing-dot-rot-v2"]',
            source,
        )


if __name__ == "__main__":
    unittest.main()
