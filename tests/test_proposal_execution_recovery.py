from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = ROOT / "app" / "proposal_execution_recovery.py"
RELAY_PATH = ROOT / "app" / "proposal_relay_runtime.py"
WORKER_PATH = ROOT / "app" / "production_worker_integration.py"
ALERT_PATH = ROOT / "app" / "execution_alert_refinement.py"
API_FINAL_PATH = ROOT / "app" / "database_runtime_hardening.py"


class ProposalExecutionRecoveryTests(unittest.TestCase):
    def test_qualified_proposal_uses_final_relay_aware_public_transport(self) -> None:
        source = PROPOSAL_PATH.read_text(encoding="utf-8")

        self.assertIn("_resilient_public_request", source)
        self.assertIn("await client.send_request(dict(request))", source)
        self.assertIn("shared_relay_transport=true", source)
        self.assertIn("direct_websocket_bypass=false", source)
        self.assertNotIn("client.pending_requests", source)
        self.assertNotIn("websocket.send", source)
        self.assertNotIn("client.ws.send", source)
        self.assertNotIn("websockets.connect", source)
        self.assertNotIn('"buy"', source)
        self.assertIn("AIDR_QUALIFIED_PROPOSAL_READY", source)
        self.assertIn("purchase_next=true", source)

    def test_proposal_relay_has_fast_primary_deadline_and_handles_send_failures(self) -> None:
        source = RELAY_PATH.read_text(encoding="utf-8")

        self.assertIn(
            'PROPOSAL_RELAY_VERSION = "two-socket-proposal-relay-v2"',
            source,
        )
        self.assertIn("PUBLIC_PROPOSAL_PRIMARY_TIMEOUT_SECONDS", source)
        self.assertIn('"2.5"', source)
        self.assertIn("await asyncio.wait_for(", source)
        self.assertIn("_primary_proposal_request", source)
        self.assertIn('"PUBLIC_PROPOSAL_SEND_FAILED"', source)
        self.assertIn('"ERROR"', source)
        self.assertIn("_proposal_error_is_relayable", source)
        self.assertIn("PROPOSAL_RELAY_RECOVERED", source)
        self.assertIn("send_failures_relayable=true", source)
        self.assertIn("financial_requests=0", source)

    def test_proposal_failure_logs_exact_exception_and_is_installed_last(self) -> None:
        source = PROPOSAL_PATH.read_text(encoding="utf-8")
        worker = WORKER_PATH.read_text(encoding="utf-8")

        self.assertIn("bot.logger.exception(", source)
        self.assertIn("AIDR_QUALIFIED_PROPOSAL_EXCEPTION", source)
        self.assertIn("error_type=%s error=%s", source)
        self.assertIn("sanitize_account_ids(str(exc))", source)
        self.assertIn(
            "immediate._provider_proposal = _qualified_provider_proposal",
            source,
        )
        self.assertNotIn("hybrid._digit_proposal", source)
        self.assertIn("scanner_unchanged=true", source)
        self.assertIn("install_proposal_execution_recovery()", worker)
        self.assertLess(
            worker.index("install_final_shared_system_strategy_clock()"),
            worker.index("install_proposal_execution_recovery()"),
        )

    def test_scanning_outcomes_are_silent_but_transport_failures_alert(self) -> None:
        source = ALERT_PATH.read_text(encoding="utf-8")

        for status in (
            "CREATED",
            "SKIP_AIDR_DIGIT_EDGE",
            "SKIP_UNPROFITABLE_QUOTE",
            "SKIP_MARKET_ARBITRATION",
            "SKIP_NEWER_SAME_ACCOUNT_GROUP_SIGNAL",
            "SKIP_MULTI_STRATEGY_EDGE",
        ):
            self.assertIn(f'"{status}"', source)

        for status in (
            "SKIP_PROVIDER_PROPOSAL_EXCEPTION",
            "SKIP_NO_SCOPE_ACCOUNTS",
            "SKIP_CONTRACT_NOT_VERIFIED",
            "PURCHASE_CONFIRMATION_MISSING",
        ):
            self.assertIn(f'"{status}"', source)

        self.assertIn("if status in _NON_ACTIONABLE_STATUSES", source)
        self.assertIn("status.startswith(", source)
        self.assertIn('"SKIP_PROVIDER_"', source)
        self.assertIn(
            'alert["title"] = "Qualified signal not purchased"',
            source,
        )

    def test_alert_uses_side_space_inline_fallback_and_permanent_close(self) -> None:
        source = ALERT_PATH.read_text(encoding="utf-8")
        final_api = API_FINAL_PATH.read_text(encoding="utf-8")

        self.assertIn('node.dataset.placement = "right"', source)
        self.assertIn('node.dataset.placement = "left"', source)
        self.assertIn('node.dataset.placement = "inline"', source)
        self.assertIn("content.prepend(node)", source)
        self.assertIn("localStorage.setItem(DISMISS_KEY", source)
        self.assertNotIn("sessionStorage", source)
        self.assertIn("rememberDismissed(id)", source)
        self.assertIn(
            'aria-label", "Dismiss this execution alert permanently"',
            source,
        )
        self.assertIn('X-FOA-Signal-Alerts": "2"', source)
        self.assertIn("install_execution_alert_refinement(app)", final_api)


if __name__ == "__main__":
    unittest.main()
