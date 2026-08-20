from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VpsPurchaseContinuityV2Contract(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_browser_liveness_has_separate_bounded_rate_bucket(self) -> None:
        source = self.text("app/vps_direct_runtime_rate_limit.py")
        backend = self.text("app/vps_backend_api.py")
        self.assertIn('"/me/direct-execution/heartbeat": 240', source)
        self.assertIn('"/me/direct-execution/checkpoint": 240', source)
        self.assertIn('"/me/direct-execution/session": 60', source)
        self.assertIn("original(request)", source)
        self.assertIn("install_vps_direct_runtime_rate_limit()", backend)
        self.assertIn("enforce_mutation_origin", self.text("app/api.py"))

    def test_concurrent_manual_stop_cannot_duplicate_insert_sentinel(self) -> None:
        source = self.text("app/direct_execution_hard_stop_state.py")
        self.assertIn("from sqlalchemy.exc import IntegrityError", source)
        self.assertIn("with session.begin_nested()", source)
        self.assertIn("session.flush()", source)
        self.assertIn("except IntegrityError", source)
        self.assertIn("populate_existing=True", source)
        self.assertNotIn("session.rollback()", source)

    def test_browser_to_vps_takeover_is_targeted_and_urgent(self) -> None:
        source = self.text("app/direct_execution_worker_fence.py")
        self.assertIn("async def _targeted_takeover", source)
        self.assertIn("private_ws.wake_private_connection(session)", source)
        self.assertIn("global_validation=false", source)
        self.assertIn("sibling_rebuild=false", source)
        self.assertIn('"targeted_urgent_no_global_validation"', source)
        refresh = source.split("async def refresh_with_direct_takeover", 1)[1].split(
            "async def fenced_exact_scope_buy", 1
        )[0]
        self.assertNotIn("validate_accounts", refresh)

    def test_otp_outer_timeout_no_longer_cancels_pooled_broker_at_eight_seconds(self) -> None:
        source = self.text("app/vps_provider_connection_resilience_v2.py")
        worker = self.text("app/custom_strategy_worker.py")
        self.assertIn("OTP_BOOTSTRAP_TIMEOUT_SECONDS = 45.0", source)
        self.assertIn("BOOTSTRAP_CONCURRENCY = 3", source)
        self.assertIn("OTP_HTTP_CONCURRENCY = 4", source)
        self.assertIn("broker_retry_boundary=authoritative", source)
        low = worker.index("install_vps_low_latency_runtime()")
        resilient = worker.index("install_vps_provider_connection_resilience_v2()")
        fence = worker.index("install_direct_execution_worker_fence()")
        self.assertGreater(resilient, low)
        self.assertGreater(fence, resilient)

    def test_terminal_policy_remains_tp_sl_or_explicit_manual_stop_only(self) -> None:
        policy = self.text("app/global_recovery_execution_policy.py")
        self.assertIn(
            '"take_profit_stop_loss_or_explicit_manual_stop_only"',
            policy,
        )
        self.assertIn("lifecycle_stop=false", policy)
        self.assertIn("enabled_preserved=true", policy)


if __name__ == "__main__":
    unittest.main()
