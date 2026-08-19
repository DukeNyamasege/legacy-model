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
        self.assertEqual(stake, 0.97)
        self.assertEqual(full, 1.93)
        self.assertEqual(target, 0.5)

    def test_split_three_never_falls_below_deriv_minimum(self) -> None:
        stake, _full, _target = equal_split_part_stake(
            recovery_basis_debt=0.10,
            proposal_profit_ratio=0.90,
            split_count=3,
        )
        self.assertEqual(DERIV_MINIMUM_STAKE, 0.50)
        self.assertEqual(stake, 0.50)

    def test_split_uses_total_loss_and_live_payout_share_without_hidden_buffer(self) -> None:
        stake, full, target = equal_split_part_stake(
            recovery_basis_debt=1.95,
            proposal_profit_ratio=0.56,
            split_count=2,
        )
        self.assertEqual(target, 0.975)
        self.assertEqual(full, 3.49)
        self.assertEqual(stake, 1.75)

    def test_real_debt_is_final_recovery_classifier(self) -> None:
        source = (ROOT / "app" / "global_recovery_execution_policy.py").read_text(encoding="utf-8")
        early = (ROOT / "app" / "custom_split_recovery_authority.py").read_text(encoding="utf-8")
        self.assertIn('"recovery_classification": "REAL_DEBT_IS_RECOVERY"', source)
        self.assertIn("if debt <= 0.009", source)
        self.assertIn("is_recovery=True", source)
        self.assertIn("legacy_cap_ignored=true", source)
        self.assertIn("actual debt forces recovery classification", early)
        self.assertIn("debt_classifier_authoritative=true", early)
        self.assertIn(
            "splitPartStake",
            (ROOT / "scripts" / "finalize-production-controls-v6.mjs").read_text(encoding="utf-8"),
        )

    def test_stale_split_basis_repairs_zero_remaining_or_unconsumed_cycle(self) -> None:
        source = (ROOT / "app" / "stale_split_basis_reconciliation_authority.py").read_text(encoding="utf-8")
        fence = (ROOT / "app" / "direct_execution_worker_fence.py").read_text(encoding="utf-8")
        self.assertIn("remaining <= 0 or remaining == split_count", source)
        self.assertIn("abs(basis - debt) > 0.009", source)
        self.assertIn("manual._write_split_remaining(self, managed_id, split_count)", source)
        self.assertIn("equal_split._write_basis_debt(self, managed_id, debt)", source)
        self.assertIn("global_policy._write_part_stake(self, managed_id, 0.0)", source)
        self.assertIn("GLOBAL_SPLIT_STALE_BASIS_REPAIRED", source)
        self.assertGreater(
            fence.index("install_stale_split_basis_reconciliation_authority()"),
            fence.index("install_global_recovery_execution_policy()"),
        )

    def test_only_tp_sl_or_explicit_manual_stop_are_terminal(self) -> None:
        source = (ROOT / "app" / "global_recovery_execution_policy.py").read_text(encoding="utf-8")
        self.assertIn('_ALLOWED_TERMINAL = {"take_profit", "stop_loss"}', source)
        self.assertIn('_MANUAL_STATUSES = {"stopped", "manual_pause"}', source)
        self.assertIn('"stopped",\n    "manual_pause",', source)
        self.assertIn("GLOBAL_AUTOMATIC_STOP_BLOCKED", source)
        self.assertIn("lifecycle_stop=false enabled_preserved=true auto_retry=true", source)
        fence = (ROOT / "app" / "direct_execution_worker_fence.py").read_text(encoding="utf-8")
        self.assertGreater(
            fence.index("install_global_recovery_execution_policy()"),
            fence.index("_direct_execution_hard_stop_fence"),
        )

    def test_legacy_manual_stop_and_start_paths_share_hard_stop_sentinel(self) -> None:
        source = (ROOT / "app" / "vps_fast_execution_controls.py").read_text(encoding="utf-8")
        self.assertIn("set_direct_hard_stop", source)
        self.assertIn("clear_direct_hard_stop", source)
        self.assertIn("_write_manual_hard_stop(managed_id, reason)", source)
        self.assertIn("_clear_manual_hard_stop(managed_id)", source)
        self.assertIn('"hard_stop": True', source)
        self.assertIn('legacy_stop_policy = "independent_hard_stop_before_account_row"', source)

    def test_repository_quarantine_and_token_faults_become_retry_not_stop(self) -> None:
        source = (ROOT / "app" / "never_auto_stop_repository_authority.py").read_text(encoding="utf-8")
        fence = (ROOT / "app" / "direct_execution_worker_fence.py").read_text(encoding="utf-8")
        self.assertIn("quarantine_as_retry", source)
        self.assertIn("discard_token_without_terminal_stop", source)
        self.assertIn("row.enabled = True", source)
        self.assertIn("automatic recovery required", source)
        self.assertIn("_explicit_manual_reason", source)
        self.assertIn("direct_hard_stop_active", source)
        self.assertIn("generic/synthetic", source)
        self.assertIn("install_never_auto_stop_repository_authority()", fence)
        self.assertLess(
            fence.index("install_never_auto_stop_repository_authority()"),
            fence.index("install_global_recovery_execution_policy()"),
        )

    def test_existing_explicit_automatic_stops_are_repaired_at_worker_start(self) -> None:
        source = (ROOT / "app" / "global_recovery_execution_policy.py").read_text(encoding="utf-8")
        self.assertIn("_EXISTING_AUTOMATIC_STOP_STATUSES", source)
        self.assertIn("_repair_existing_automatic_stops", source)
        self.assertIn("Existing automatic execution stop restored to retry", source)
        self.assertNotIn('"real_disabled",\n}', source)

    def test_fresh_start_clears_stale_checkpoint_but_reset_is_history_only(self) -> None:
        source = (ROOT / "app" / "vps_runtime_policy_hotfix.py").read_text(encoding="utf-8")
        controls = (ROOT / "app" / "vps_fast_execution_controls.py").read_text(encoding="utf-8")
        browser = (ROOT / "dashboard" / "deriv-direct-execution-v1.js").read_text(encoding="utf-8")
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

        server_start = controls.index("def fast_clear_personal_trades(")
        server_end = controls.index("app.state.vps_fast_execution_controls_installed", server_start)
        clear_route = controls[server_start:server_end]
        self.assertIn("HISTORY ONLY", clear_route)
        self.assertIn('"financial_state_preserved": True', clear_route)
        self.assertNotIn("_reset_risk_state_bounded(session, managed_id)", clear_route)
        self.assertNotIn("_delete_runtime_preferences_bounded(session, managed_id)", clear_route)

        start = browser.index("function clearLocalTrades()")
        end = browser.index("function normalizeCondition", start)
        clear_body = browser[start:end]
        self.assertIn("HISTORY ONLY", clear_body)
        self.assertNotIn("state.recoveryDebt = 0", clear_body)
        self.assertNotIn("state.sessionProfit = 0", clear_body)
        self.assertNotIn("state.consecutiveLosses = 0", clear_body)
        self.assertNotIn("state.virtualMode = false", clear_body)

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

    def test_trade_metrics_are_per_account_and_incremental_after_seed(self) -> None:
        source = (ROOT / "app" / "account_trade_metrics_authority.py").read_text(encoding="utf-8")
        self.assertIn("Trade.managed_account_id == int(managed_id)", source)
        self.assertIn("Trade.purchase_time >= started_at", source)
        self.assertIn("row.cumulative_profit", source)
        self.assertIn("account_trade_metrics:v1:", source)
        self.assertIn("_apply_incremental_trade_metrics", source)
        self.assertIn("startup_repair_then_incremental_cursor", source)
        self.assertNotIn("BotState", source)

    def test_status_poll_load_and_database_pool_are_bounded(self) -> None:
        finalizer = (ROOT / "scripts" / "finalize-global-recovery-v1.mjs").read_text(encoding="utf-8")
        api = (ROOT / "app" / "vps_runtime_policy_hotfix.py").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        self.assertIn("statusTimerPattern", finalizer)
        self.assertIn('"10000"', finalizer)
        self.assertIn("direct status polling throttle was not installed", finalizer)
        self.assertIn("RuntimePreference.preference_key.in_((owner_key, stop_key))", api)
        self.assertIn("one_account_read_one_batched_preference_read", api)
        self.assertIn("DATABASE_POOL_SIZE", compose)
        self.assertIn("DATABASE_POOL_TIMEOUT_SECONDS", compose)

    def test_ordinary_ws_reconnect_is_fast_but_rate_limit_backoff_is_retained(self) -> None:
        compose = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        self.assertIn("PRIVATE_WS_NORMAL_RECONNECT_BASE_SECONDS", compose)
        self.assertIn("PRIVATE_WS_NORMAL_RECONNECT_MAX_SECONDS", compose)
        self.assertIn("VPS_PRIVATE_WS_NORMAL_RECONNECT_MAX_SECONDS:-4", compose)
        self.assertIn("PRIVATE_WS_RATE_LIMIT_BACKOFF_SECONDS", compose)
        self.assertIn("PRIVATE_WS_MAX_BACKOFF_SECONDS", compose)

    def test_runtime_report_matches_exact_managed_id_suffix_without_unsafe_cast(self) -> None:
        source = (ROOT / "scripts" / "collect_account_runtime_report.sh").read_text(encoding="utf-8")
        self.assertIn("substring(preference_key from ':([0-9]+)$')::integer IN", source)
        self.assertNotIn("replace('${ID_LIST}', ',', '|')", source)
        self.assertNotIn("split_part(preference_key", source)


if __name__ == "__main__":
    unittest.main()
