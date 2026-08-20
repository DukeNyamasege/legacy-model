from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "app" / "tp_sl_manual_only_authority.py"
STALE_INSTALLER = ROOT / "app" / "stale_split_basis_reconciliation_authority.py"


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_only_tp_sl_are_automatic_terminal_targets() -> None:
    text = source(AUTHORITY)
    assert '_TARGET_STOPS = {"take_profit", "stop_loss"}' in text
    assert "direct_hard_stop_active" in text
    assert "current in _TARGET_STOPS" in text


def test_automatic_runtime_failures_are_retryable_not_terminal() -> None:
    text = source(AUTHORITY)
    for status in (
        "error",
        "credential_error",
        "invalid_account",
        "token_required",
        "bulk_execution_pat_required",
        "contract_unavailable",
        "purchase_registration_error",
        "insufficient_balance",
        "purchase_insufficient_balance",
        "duplicate",
    ):
        assert f'"{status}"' in text
    assert "TP_SL_MANUAL_ONLY_STOP_BLOCKED" in text
    assert "row.enabled = True" in text
    assert "automatic recovery will retry" in text


def test_manual_stop_requires_durable_hard_stop_not_reason_phrases() -> None:
    text = source(AUTHORITY)
    assert "direct_hard_stop_active(session" in text
    assert "start is required before execution" not in text.lower()
    assert "auto trading stopped for this account mode" not in text.lower()


def test_direct_fail_closed_is_converted_to_runtime_repair() -> None:
    text = source(AUTHORITY)
    assert "direct_runtime._fail_closed = _final_fail_closed" in text
    assert "seamless._schedule_runtime_repair" in text
    assert "lifecycle_stop=false auto_retry=true" in text


def test_direct_database_automatic_disables_are_repaired() -> None:
    text = source(AUTHORITY)
    assert "ManagedAccount.enabled.is_(False)" in text
    assert "ManagedAccount.execution_status.in_" in text
    assert "TP_SL_MANUAL_ONLY_STALE_AUTO_STOP_REPAIRED" in text


def test_final_authority_is_installed_after_global_and_stale_recovery_layers() -> None:
    text = source(STALE_INSTALLER)
    marker = "install_tp_sl_manual_only_authority()"
    assert marker in text
    assert text.rfind(marker) > text.rfind("RFDir5Repository._stale_split_basis_reconciliation_installed = True")
