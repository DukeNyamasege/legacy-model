from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = ROOT / "app" / "proposal_execution_recovery.py"
RELAY_PATH = ROOT / "app" / "proposal_relay_runtime.py"
WORKER_PATH = ROOT / "app" / "production_worker_integration.py"
ALERT_PATH = ROOT / "app" / "execution_alert_refinement.py"
API_FINAL_PATH = ROOT / "app" / "database_runtime_hardening.py"


def test_qualified_proposal_uses_final_relay_aware_public_transport() -> None:
    source = PROPOSAL_PATH.read_text(encoding="utf-8")

    assert "_resilient_public_request" in source
    assert "await client.send_request(dict(request))" in source
    assert "shared_relay_transport=true" in source
    assert "direct_websocket_bypass=false" in source
    assert "client.pending_requests" not in source
    assert "websocket.send" not in source
    assert "client.ws" not in source
    assert "websockets.connect" not in source
    assert '"buy"' not in source
    assert "AIDR_QUALIFIED_PROPOSAL_READY" in source
    assert "purchase_next=true" in source


def test_proposal_relay_has_fast_primary_deadline_and_handles_send_failures() -> None:
    source = RELAY_PATH.read_text(encoding="utf-8")

    assert 'PROPOSAL_RELAY_VERSION = "two-socket-proposal-relay-v2"' in source
    assert "PUBLIC_PROPOSAL_PRIMARY_TIMEOUT_SECONDS" in source
    assert '"2.5"' in source
    assert "await asyncio.wait_for(" in source
    assert "_primary_proposal_request" in source
    assert '"PUBLIC_PROPOSAL_SEND_FAILED"' in source
    assert '"ERROR"' in source
    assert "_proposal_error_is_relayable" in source
    assert "PROPOSAL_RELAY_RECOVERED" in source
    assert "send_failures_relayable=true" in source
    assert "financial_requests=0" in source


def test_proposal_failure_logs_exact_exception_and_is_installed_last() -> None:
    source = PROPOSAL_PATH.read_text(encoding="utf-8")
    worker = WORKER_PATH.read_text(encoding="utf-8")

    assert "bot.logger.exception(" in source
    assert "AIDR_QUALIFIED_PROPOSAL_EXCEPTION" in source
    assert "error_type=%s error=%s" in source
    assert "sanitize_account_ids(str(exc))" in source
    assert "immediate._provider_proposal = _qualified_provider_proposal" in source
    assert "hybrid._digit_proposal" not in source
    assert "scanner_unchanged=true" in source
    assert "install_proposal_execution_recovery()" in worker
    assert worker.index("install_final_shared_system_strategy_clock()") < worker.index(
        "install_proposal_execution_recovery()"
    )


def test_scanning_outcomes_are_silent_but_transport_failures_alert() -> None:
    source = ALERT_PATH.read_text(encoding="utf-8")

    for status in (
        "CREATED",
        "SKIP_AIDR_DIGIT_EDGE",
        "SKIP_UNPROFITABLE_QUOTE",
        "SKIP_MARKET_ARBITRATION",
        "SKIP_NEWER_SAME_ACCOUNT_GROUP_SIGNAL",
        "SKIP_MULTI_STRATEGY_EDGE",
    ):
        assert f'"{status}"' in source

    for status in (
        "SKIP_PROVIDER_PROPOSAL_EXCEPTION",
        "SKIP_NO_SCOPE_ACCOUNTS",
        "SKIP_CONTRACT_NOT_VERIFIED",
        "PURCHASE_CONFIRMATION_MISSING",
    ):
        assert f'"{status}"' in source

    assert "if status in _NON_ACTIONABLE_STATUSES" in source
    assert "status.startswith(" in source
    assert '"SKIP_PROVIDER_"' in source
    assert 'alert["title"] = "Qualified signal not purchased"' in source


def test_alert_uses_side_space_inline_fallback_and_permanent_close() -> None:
    source = ALERT_PATH.read_text(encoding="utf-8")
    final_api = API_FINAL_PATH.read_text(encoding="utf-8")

    assert 'node.dataset.placement = "right"' in source
    assert 'node.dataset.placement = "left"' in source
    assert 'node.dataset.placement = "inline"' in source
    assert "content.prepend(node)" in source
    assert "localStorage.setItem(DISMISS_KEY" in source
    assert "sessionStorage" not in source
    assert "rememberDismissed(id)" in source
    assert 'aria-label", "Dismiss this execution alert permanently"' in source
    assert 'X-FOA-Signal-Alerts": "2"' in source
    assert "install_execution_alert_refinement(app)" in final_api
