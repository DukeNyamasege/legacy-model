from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "app" / "tp_sl_manual_only_authority.py"
STALE_INSTALLER = ROOT / "app" / "stale_split_basis_reconciliation_authority.py"


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TpSlManualOnlyAuthorityTests(unittest.TestCase):
    def test_only_tp_sl_are_automatic_terminal_targets(self) -> None:
        text = source(AUTHORITY)
        self.assertIn('_TARGET_STOPS = {"take_profit", "stop_loss"}', text)
        self.assertIn("direct_hard_stop_active", text)
        self.assertIn("current in _TARGET_STOPS", text)

    def test_automatic_runtime_failures_are_retryable_not_terminal(self) -> None:
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
            self.assertIn(f'"{status}"', text)
        self.assertIn("TP_SL_MANUAL_ONLY_STOP_BLOCKED", text)
        self.assertIn("row.enabled = True", text)
        self.assertIn("automatic recovery will retry", text)

    def test_manual_stop_requires_durable_hard_stop_not_reason_phrases(self) -> None:
        text = source(AUTHORITY)
        self.assertIn("direct_hard_stop_active(session", text)
        self.assertNotIn("start is required before execution", text.lower())
        self.assertNotIn("auto trading stopped for this account mode", text.lower())

    def test_direct_fail_closed_is_converted_to_runtime_repair(self) -> None:
        text = source(AUTHORITY)
        self.assertIn("direct_runtime._fail_closed = _final_fail_closed", text)
        self.assertIn("seamless._schedule_runtime_repair", text)
        self.assertIn("lifecycle_stop=false auto_retry=true", text)

    def test_direct_database_automatic_disables_are_repaired_continuously(self) -> None:
        text = source(AUTHORITY)
        self.assertIn("ManagedAccount.enabled.is_(False)", text)
        self.assertIn("ManagedAccount.execution_status.in_", text)
        self.assertIn("TP_SL_MANUAL_ONLY_STALE_AUTO_STOP_REPAIRED", text)
        self.assertIn("while True:", text)

    def test_final_authority_is_installed_after_global_and_stale_recovery_layers(self) -> None:
        text = source(STALE_INSTALLER)
        marker = "install_tp_sl_manual_only_authority()"
        self.assertIn(marker, text)
        self.assertGreater(
            text.rfind(marker),
            text.rfind("RFDir5Repository._stale_split_basis_reconciliation_installed = True"),
        )


if __name__ == "__main__":
    unittest.main()
