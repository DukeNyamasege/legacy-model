from __future__ import annotations

import unittest
from pathlib import Path

from app.global_recovery_execution_policy import (
    DERIV_MINIMUM_STAKE,
    equal_split_part_stake,
)


ROOT = Path(__file__).resolve().parents[1]


class GlobalRecoveryPolicyTests(unittest.TestCase):
    def test_split_two_uses_one_equal_fixed_part_stake(self) -> None:
        stake, full, target = equal_split_part_stake(
            recovery_basis_debt=1.0,
            proposal_profit_ratio=0.52,
            split_count=2,
        )
        self.assertEqual(stake, 1.02)
        self.assertEqual(full, 2.04)
        self.assertGreater(target, 0.5)

    def test_split_three_never_falls_below_deriv_minimum(self) -> None:
        stake, _full, _target = equal_split_part_stake(
            recovery_basis_debt=0.10,
            proposal_profit_ratio=0.90,
            split_count=3,
        )
        self.assertEqual(DERIV_MINIMUM_STAKE, 0.50)
        self.assertEqual(stake, 0.50)

    def test_real_debt_is_final_recovery_classifier(self) -> None:
        source = (ROOT / "app" / "global_recovery_execution_policy.py").read_text(encoding="utf-8")
        self.assertIn('"recovery_classification": "REAL_DEBT_IS_RECOVERY"', source)
        self.assertIn("if debt <= 0.009", source)
        self.assertIn("is_recovery=True", source)
        self.assertIn("legacy_cap_ignored=true", source)
        self.assertIn("splitPartStake", (ROOT / "scripts" / "finalize-production-controls-v6.mjs").read_text(encoding="utf-8"))

    def test_only_tp_sl_or_explicit_manual_stop_are_terminal(self) -> None:
        source = (ROOT / "app" / "global_recovery_execution_policy.py").read_text(encoding="utf-8")
        self.assertIn('_ALLOWED_TERMINAL = {"take_profit", "stop_loss"}', source)
        self.assertIn('_MANUAL_STATUSES = {"stopped", "manual_pause"}', source)
        self.assertIn("GLOBAL_AUTOMATIC_STOP_BLOCKED", source)
        self.assertIn("lifecycle_stop=false enabled_preserved=true auto_retry=true", source)
        fence = (ROOT / "app" / "direct_execution_worker_fence.py").read_text(encoding="utf-8")
        self.assertGreater(
            fence.index("install_global_recovery_execution_policy()"),
            fence.index("_direct_execution_hard_stop_fence"),
        )

    def test_fresh_start_clears_stale_checkpoint_but_reset_is_history_only(self) -> None:
        source = (ROOT / "app" / "vps_runtime_policy_hotfix.py").read_text(encoding="utf-8")
        for prefix in (
            "direct_execution:checkpoint:v1:",
            "custom_equal_split_basis_debt:",
            "custom_equal_split_part_stake:",
            "manual_martingale_v2_split_remaining:",
        ):
            self.assertIn(prefix, source)
        self.assertIn('"/me/direct-execution/arm"', source)
        self.assertNotIn('"/me/clear-trades",', source)
        self.assertIn('reset_trades_financial_state_policy = "history_only"', source)

    def test_browser_and_server_handoff_preserve_fixed_split_stake(self) -> None:
        checkpoint_js = (ROOT / "dashboard" / "direct-continuity-checkpoint-v1.js").read_text(encoding="utf-8")
        checkpoint_api = (ROOT / "app" / "vps_direct_execution_checkpoint.py").read_text(encoding="utf-8")
        finalizer = (ROOT / "scripts" / "finalize-production-controls-v6b.mjs").read_text(encoding="utf-8")
        self.assertIn("split_part_stake", checkpoint_js)
        self.assertIn("split_part_stake", checkpoint_api)
        self.assertIn("split_part_stake: state.splitPartStake", finalizer)

    def test_duplicate_identity_is_canonical_not_deleted(self) -> None:
        source = (ROOT / "app" / "account_identity_canonical_authority.py").read_text(encoding="utf-8")
        self.assertIn("decrypt_auth_payload", source)
        self.assertIn("_canonical_rows", source)
        self.assertIn("add_or_refresh_canonical_account", source)
        self.assertNotIn("session.delete", source)
        self.assertNotIn("delete(ManagedAccount", source)

    def test_trade_metrics_are_repaired_per_account_session(self) -> None:
        source = (ROOT / "app" / "account_trade_metrics_authority.py").read_text(encoding="utf-8")
        self.assertIn("Trade.managed_account_id == int(managed_id)", source)
        self.assertIn("Trade.purchase_time >= started_at", source)
        self.assertIn("row.cumulative_profit", source)
        self.assertNotIn("BotState", source)

    def test_status_poll_load_and_status_query_are_bounded(self) -> None:
        finalizer = (ROOT / "scripts" / "finalize-global-recovery-v1.mjs").read_text(encoding="utf-8")
        api = (ROOT / "app" / "vps_runtime_policy_hotfix.py").read_text(encoding="utf-8")
        self.assertIn('"  }, 10000);"', finalizer)
        self.assertIn("RuntimePreference.preference_key.in_((owner_key, stop_key))", api)
        self.assertIn("one_account_read_one_batched_preference_read", api)

    def test_runtime_report_matches_exact_managed_id_suffix(self) -> None:
        source = (ROOT / "scripts" / "collect_account_runtime_report.sh").read_text(encoding="utf-8")
        self.assertIn("preference_key ~ ':[0-9]+$'", source)
        self.assertIn("split_part(preference_key", source)
        self.assertNotIn("replace('${ID_LIST}', ',', '|')", source)


if __name__ == "__main__":
    unittest.main()
