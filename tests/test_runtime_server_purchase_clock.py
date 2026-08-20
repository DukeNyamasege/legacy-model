from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "app" / "vps_cross_device_runtime_sync.py"
FINALIZER = ROOT / "scripts" / "finalize-runtime-safety-v2b.mjs"
DOCKERFILE = ROOT / "Dockerfile.frontend"


class RuntimeServerPurchaseClockTests(unittest.TestCase):
    def test_runtime_sync_exposes_latest_server_purchase(self) -> None:
        source = SYNC.read_text(encoding="utf-8")
        self.assertIn("Trade.managed_account_id == managed_id", source)
        self.assertIn("Trade.purchase_time.desc()", source)
        self.assertIn('"last_purchase_at"', source)
        self.assertIn("latest_trade.purchase_time.isoformat()", source)

    def test_no_purchase_clock_uses_browser_or_server_purchase(self) -> None:
        source = FINALIZER.read_text(encoding="utf-8")
        for marker in (
            "browserAnchor",
            "runtimeSyncSnapshot().last_purchase_at",
            "serverPurchaseAt",
            "Math.max(browserAnchor, serverPurchaseAt)",
            "NO TRADE PURCHASED AFTER 60 SECONDS",
        ):
            self.assertIn(marker, source)

    def test_v2b_runs_after_main_runtime_safety_gate(self) -> None:
        docker = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("COPY scripts/finalize-runtime-safety-v2b.mjs", docker)
        self.assertIn("node --check scripts/finalize-runtime-safety-v2b.mjs", docker)
        main_safety = docker.rfind("node scripts/finalize-runtime-safety-v2.mjs")
        server_clock = docker.rfind("node scripts/finalize-runtime-safety-v2b.mjs")
        self.assertGreater(main_safety, -1)
        self.assertGreater(server_clock, main_safety)


if __name__ == "__main__":
    unittest.main()
