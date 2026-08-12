from __future__ import annotations

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class SeamlessRuntimeControlTests(TestCase):
    def test_dashboard_uses_one_bounded_live_snapshot(self) -> None:
        source = (ROOT / "app" / "seamless_dashboard_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"/me/live-snapshot"', source)
        self.assertIn('_remove_route(app, "/metrics/summary", "GET")', source)
        self.assertIn("cache_only", source)
        self.assertIn(".limit(_RECENT_LIMIT)", source)
        self.assertNotIn("build_execution_summary", source)

    def test_browser_broker_coalesces_and_recovers_without_blocking_notice(self) -> None:
        source = (ROOT / "dashboard" / "seamless-runtime-client.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('fetchJSONNative("/me/live-snapshot")', source)
        self.assertIn("const inflight = new Map()", source)
        self.assertIn("scheduleReconnect", source)
        self.assertIn("foa:runtime-mutation-confirmed", source)
        self.assertIn("LIVE REFRESH DELAYED - showing last known dashboard data.", source)
        self.assertIn("node.remove()", source)

    def test_seamless_broker_loads_before_current_runtime_client(self) -> None:
        source = (ROOT / "app" / "builder_first_dashboard_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_seamless_dashboard_runtime(app)", source)
        self.assertIn("live-dashboard-authority-6", source)
        seamless = source.index('seamless_script = f\'<script src="/ui/seamless-runtime-client.js')
        direct = source.index('direct_script = f\'<script src="/ui/custom-runtime-client.js')
        self.assertLess(seamless, direct)

    def test_transient_ws_failure_reconnects_without_disabling_account(self) -> None:
        source = (ROOT / "app" / "custom_strategy_transport_resilience.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("CUSTOM_PROPOSAL_RETRY_AFTER_RECONNECT", source)
        self.assertIn("CUSTOM_STRATEGY_TRANSIENT_RECOVERY", source)
        self.assertIn("account_disabled=false", source)
        self.assertIn("blind_retry=false", source)
        self.assertIn("CUSTOM_BUY_ACK_HOLD_SECONDS", source)
        self.assertIn("private_rate._normal_backoff = _fast_private_normal_backoff", source)
        self.assertNotIn("update_managed_account(int(managed_id), enabled=False)", source)

    def test_planned_public_restart_has_subsecond_reconnect_path(self) -> None:
        source = (ROOT / "app" / "public_websocket_resilience.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("custom_market_set_changed", source)
        self.assertIn("PUBLIC_STREAM_FAST_RECONNECT", source)
        self.assertIn("DERIV_PUBLIC_WS_FAST_RECONNECT_SECONDS", source)
        self.assertIn("0.35", source)

    def test_worker_installs_transport_recovery_after_exact_account_fix(self) -> None:
        source = (ROOT / "app" / "custom_strategy_worker.py").read_text(
            encoding="utf-8"
        )
        exact = source.index("install_custom_strategy_current_runtime_fix()")
        resilient = source.index("install_custom_strategy_transport_resilience()")
        self.assertLess(exact, resilient)

    def test_env_recovery_never_prints_secrets_or_touches_volumes(self) -> None:
        source = (ROOT / "scripts" / "recover_vps_env_from_running_stack.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("docker inspect", source)
        self.assertIn('chmod 600 "$TARGET"', source)
        self.assertIn("Compose validation: OK", source)
        self.assertNotIn("docker volume prune", source)
        self.assertNotIn("docker system prune", source)
        self.assertNotIn('cat "$TARGET"', source)


if __name__ == "__main__":
    import unittest

    unittest.main()
