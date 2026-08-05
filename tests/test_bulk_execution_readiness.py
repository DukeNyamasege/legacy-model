from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readiness_audit_never_prints_raw_tokens() -> None:
    source = (ROOT / "scripts" / "audit_bulk_execution_readiness.py").read_text(
        encoding="utf-8"
    )
    assert "raw_tokens_printed=false" in source
    assert "_fingerprint" in source
    assert "mask_account_id" in source
    assert "runtime_bulk_capable" in source
    assert "decrypt_failed" in source
    assert "candidate_status_counts" in source
    assert "latest_bulk_batches" in source
    assert "BLOCKER=" in source
    assert "print(api_token)" not in source
    assert "print(effective_token)" not in source


def test_personal_dashboard_heals_only_stale_non_rejected_token_status() -> None:
    source = (ROOT / "app" / "personal_me_session_fix.py").read_text(
        encoding="utf-8"
    )
    assert '_STALE_TOKEN_STATUSES = {"token_required", "bulk_execution_pat_required"}' in source
    assert "execution_token_was_rejected" in source
    assert "has_personal_trading_api_token" in source
    assert "Stored Deriv API token detected; runtime validation pending" in source
    assert "_reconcile_stale_token_status(request)" in source
    assert '"credential_error"' not in source.split("_STALE_TOKEN_STATUSES", 1)[1].split("\n", 1)[0]
