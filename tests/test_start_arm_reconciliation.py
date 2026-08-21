from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartArmReconciliationTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_timed_out_arm_reconciles_same_server_epoch_before_reposting(self) -> None:
        source = self.text("scripts/finalize-start-arm-reconciliation-v1.mjs")
        self.assertIn('apiPath("/me/runtime-sync")', source)
        self.assertIn('String(payload?.epoch || "") === String(epoch || "")', source)
        self.assertIn('const browserOwned = owner === "browser"', source)
        self.assertIn('payload?.hard_stop !== true', source)
        self.assertIn('payload?.enabled !== false', source)
        self.assertIn('if (await reconcileArm(epoch, 1)) return true;', source)
        self.assertIn('if (await reconcileArm(epoch, 4)) return true;', source)
        self.assertIn('20000', source)

    def test_reconciliation_never_weakens_financial_ownership(self) -> None:
        source = self.text("scripts/finalize-start-arm-reconciliation-v1.mjs")
        self.assertIn('if (!sameEpoch || !browserOwned || !financiallyAllowed) return false;', source)
        self.assertIn('if (!state.running || state.epoch !== epoch || state.ownerLost)', source)
        self.assertIn('state.armed = true;', source)
        self.assertIn('state.leaseMs = Number.MAX_SAFE_INTEGER;', source)
        self.assertIn('must not reintroduce periodic financial heartbeat traffic', source)

    def test_unarmed_diagnosis_exposes_current_start_error_without_secrets(self) -> None:
        source = self.text("scripts/finalize-start-arm-reconciliation-v1.mjs")
        self.assertIn('Last Start error:', source)
        self.assertIn('Bearer [redacted]', source)
        self.assertIn('otp=[redacted]', source)
        self.assertIn('diagnostics.last_execution_error', source)
        self.assertIn('Patch that finalized engine directly', source)

    def test_reconciliation_is_final_frontend_production_layer(self) -> None:
        docker = self.text("Dockerfile.frontend")
        self.assertIn(
            "COPY scripts/finalize-start-arm-reconciliation-v1.mjs ./scripts/finalize-start-arm-reconciliation-v1.mjs",
            docker,
        )
        self.assertIn("node --check scripts/finalize-start-arm-reconciliation-v1.mjs", docker)
        marketing = docker.rfind("node scripts/finalize-marketing-ui-layout-v1.mjs")
        reconciliation = docker.rfind("node scripts/finalize-start-arm-reconciliation-v1.mjs")
        self.assertGreater(reconciliation, marketing)

    def test_execution_engine_asset_is_cache_busted(self) -> None:
        source = self.text("scripts/finalize-start-arm-reconciliation-v1.mjs")
        self.assertIn("20260821-start-arm-reconcile-v2", source)
        self.assertNotIn("start-arm-diagnostics-v1", source)
        self.assertNotIn("start-arm-shell-v1", source)


if __name__ == "__main__":
    unittest.main()
