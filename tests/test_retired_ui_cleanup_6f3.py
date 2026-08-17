from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"


class RetiredUiCleanup6F3Tests(unittest.TestCase):
    def test_retired_presentation_files_are_physically_removed(self) -> None:
        retired = (
            "final-ui-shell-v1.js",
            "final-ui-shell-v1.css",
            "premium-subscription-action6e.js",
            "premium-subscription-action6e.css",
            "vps-api-boundary.js",
            "vps-realtime-client.js",
            "automation-home-v1.js",
            "automation-home-v1.css",
            "text-to-strategy-v1.js",
            "text-to-strategy-v1.css",
            "strategy-ready-v1.js",
            "strategy-ready-v1.css",
            "timezone-schedule-v1.js",
            "timezone-schedule-v1.css",
            "automation-scheduler-action5.js",
            "automation-scheduler-action5.css",
            "account-bound-risk-notice.js",
            "account-bound-risk-notice.css",
            "account-lifecycle.js",
            "builder-edit-stability.js",
            "custom-martingale.js",
            "custom-runtime-client.js",
            "dashboard-actions-v2.js",
            "dashboard-v2.js",
            "dashboard-v2.css",
            "data-consistency.js",
            "execution-status-banner.js",
            "final-dashboard-authority.js",
            "final-dashboard-authority.css",
        )
        for name in retired:
            self.assertFalse((DASHBOARD / name).exists(), msg=f"retired UI source remains: {name}")

    def test_final_presentation_authorities_remain(self) -> None:
        required = (
            "index.html",
            "final-ui-shell-v2.js",
            "final-ui-shell-v2.css",
            "final-premium-6f3.js",
            "final-premium-6f3.css",
            "vps-api-boundary-v2.js",
            "vps-realtime-client-v2.js",
        )
        for name in required:
            self.assertTrue((DASHBOARD / name).is_file(), msg=f"final UI authority missing: {name}")


if __name__ == "__main__":
    unittest.main()
