from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.deriv_rate_limit_circuit import (
    RATE_LIMIT_BASE_SECONDS,
    _cooldown_payload,
    _error_code,
    _open_circuit,
)
from app.public_websocket_resilience import (
    PUBLIC_WS_BACKOFF_MAX_SECONDS,
    PUBLIC_WS_RATE_LIMIT_BACKOFF_SECONDS,
    _preflight_stream_disabled,
    _retry_delay,
)


ROOT = Path(__file__).resolve().parents[1]


class PublicWebSocketResilienceTests(unittest.TestCase):
    def test_preflight_worker_never_opens_duplicate_external_stream(self) -> None:
        with patch.dict(os.environ, {"DEPLOYMENT_ID": "preflight-worker"}, clear=False):
            self.assertTrue(_preflight_stream_disabled())

    def test_production_worker_keeps_public_stream_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DEPLOYMENT_ID": "production-worker",
                "DERIV_PUBLIC_WS_PREFLIGHT_DISABLED": "false",
            },
            clear=False,
        ):
            self.assertFalse(_preflight_stream_disabled())

    def test_rate_limit_backoff_is_long_and_bounded(self) -> None:
        delay = _retry_delay(1, rate_limited=True)
        self.assertGreaterEqual(delay, min(PUBLIC_WS_RATE_LIMIT_BACKOFF_SECONDS, PUBLIC_WS_BACKOFF_MAX_SECONDS))
        self.assertLessEqual(delay, PUBLIC_WS_BACKOFF_MAX_SECONDS)

    def test_normal_backoff_grows_but_stays_bounded(self) -> None:
        first = _retry_delay(1, rate_limited=False)
        later = _retry_delay(6, rate_limited=False)
        self.assertGreaterEqual(later, first)
        self.assertLessEqual(later, PUBLIC_WS_BACKOFF_MAX_SECONDS)


class DerivRateLimitCircuitTests(unittest.TestCase):
    def test_cooldown_response_sends_no_network_request(self) -> None:
        payload = _cooldown_payload(91.2)
        self.assertEqual(_error_code(payload), "RATE_LIMITED")
        self.assertGreaterEqual(payload["error"]["retry_after_seconds"], 91)
        self.assertIn("no network request", payload["error"]["message"])

    def test_repeated_rate_limits_extend_shared_circuit(self) -> None:
        request_broker = type("Broker", (), {})()
        first = _open_circuit(request_broker)
        first_until = request_broker._rate_limit_until
        second = _open_circuit(request_broker)
        self.assertGreaterEqual(first, RATE_LIMIT_BASE_SECONDS)
        self.assertGreaterEqual(second, first)
        self.assertGreaterEqual(request_broker._rate_limit_until, first_until)
        self.assertGreater(request_broker._rate_limit_until, time.monotonic())


class DeploymentSourceInvariantTests(unittest.TestCase):
    def test_worker_installs_circuit_and_public_resilience_before_bot(self) -> None:
        source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        broker = source.index("install_deriv_request_broker()")
        circuit = source.index("install_deriv_rate_limit_circuit()")
        public = source.index("install_public_websocket_resilience()")
        bot = source.index("bot = RFDir5TradingBot()")
        self.assertLess(broker, circuit)
        self.assertLess(circuit, public)
        self.assertLess(public, bot)

    def test_smoke_retries_provider_and_skips_it_only_in_preflight(self) -> None:
        source = (ROOT / "scripts" / "production_smoke.py").read_text(encoding="utf-8")
        self.assertIn("DEPLOY_PROVIDER_WS_ATTEMPTS", source)
        self.assertIn("DERIV_PUBLIC_WS_SMOKE_RETRY", source)
        self.assertIn("bounded attempts", source)
        self.assertIn('deployment_id.startswith("preflight-api")', source)
        self.assertIn("isolated_preflight_avoids_duplicate_provider_connection", source)

    def test_financial_execution_remains_private_websocket_only(self) -> None:
        public_source = (ROOT / "app" / "public_websocket_resilience.py").read_text(encoding="utf-8")
        circuit_source = (ROOT / "app" / "deriv_rate_limit_circuit.py").read_text(encoding="utf-8")
        self.assertNotIn("bulk-purchase", public_source)
        self.assertIn("PRIVATE_WEBSOCKET_ONLY", circuit_source)
        self.assertIn("bulk_purchase=false", circuit_source)
        self.assertIn("copy_trading=false", circuit_source)


if __name__ == "__main__":
    unittest.main()
