from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "app" / "strategy_v2_final_ui.py"


def _source() -> str:
    return SOURCE_PATH.read_text(encoding="utf-8")


def test_signal_execution_alert_backend_is_account_scoped() -> None:
    source = _source()
    tree = ast.parse(source)

    assert "ALERT_LIFETIME_SECONDS = 180" in source
    assert "CREATED_GRACE_SECONDS = 12" in source
    assert '@app.get("/me/execution-alert"' in source
    assert "base_api.get_current_account(request)" in source
    assert "read_strategy(base_api.DATABASE, managed_id)" in source
    assert "Trade.managed_account_id == managed_id" in source
    assert "CandidateSignalRecord.generated_timestamp >= cutoff" in source
    assert "ModelDecisionRecord.signal_id.in_(signal_ids)" in source
    assert "signal_id in purchased_ids" in source
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "personal_execution_alert"
        for node in ast.walk(tree)
    )


def test_signal_kill_reasons_cover_observed_and_pipeline_failures() -> None:
    source = _source()

    assert "SKIP_NEWER_SAME_ACCOUNT_GROUP_SIGNAL" in source
    assert "newer signal for the same account group replaced this signal" in source
    assert "CREATED_NOT_CONSUMED" in source
    assert "PROPOSAL_RESPONSE_MISSING" in source
    assert "PROPOSAL_NOT_PURCHASED" in source
    assert "PURCHASE_CONFIRMATION_MISSING" in source
    assert "CONSUMED_WITHOUT_PURCHASE" in source
    # The runtime sentence is intentionally split across adjacent Python string
    # literals for readability, so validate both source fragments separately.
    assert "this account was not selected in " in source
    assert "the current rotating execution cohort" in source
    assert '"title": "Signal killed — contract not purchased"' in source


def test_dashboard_alert_is_live_accessible_and_expires_after_three_minutes() -> None:
    source = _source()

    assert "FOA_SIGNAL_EXECUTION_ALERTS" in source
    assert "/me/execution-alert?ts=" in source
    assert 'node.setAttribute("aria-live", "assertive")' in source
    assert 'card.setAttribute("role", "alert")' in source
    assert "sessionStorage.setItem(DISMISS_KEY, id)" in source
    assert "hideTimer = setTimeout(clearAlert, remaining)" in source
    assert "180000" in source
    assert "X-FOA-Signal-Alerts" in source
    assert "foa-execution-alert-host" in source
