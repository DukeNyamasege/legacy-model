from __future__ import annotations

from pathlib import Path
import unittest

from app.custom_split_cap_defaults_authority import _default_recovery_cap
from app.custom_virtual_post_loss_barrier_authority import _tick_epoch


ROOT = Path(__file__).resolve().parents[1]
CAP_AUTHORITY = ROOT / "app" / "custom_split_cap_defaults_authority.py"
VIRTUAL_AUTHORITY = ROOT / "app" / "custom_virtual_post_loss_barrier_authority.py"
WORKER = ROOT / "app" / "custom_strategy_worker.py"
KPI_JS = ROOT / "dashboard" / "virtual-kpi-neutrality.js"
INDEX = ROOT / "dashboard" / "index.html"


class PostLossSplitAndVirtualNeutralityTests(unittest.TestCase):
    def test_omitted_split_cap_uses_canonical_defaults_not_zero_percent(self) -> None:
        cap = _default_recovery_cap(current_balance=8274.93, base_stake=0.50)
        self.assertGreater(cap, 0.50)
        self.assertAlmostEqual(cap, 827.493, places=3)
        # The screenshot-size recovery stake must not be rejected merely because
        # AccountExecutionSession omitted optional cap kwargs.
        self.assertLess(0.91, cap)

        source = CAP_AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("_DEFAULT_MAX_RECOVERY_BALANCE_FRACTION = 0.10", source)
        self.assertIn("_DEFAULT_MINIMUM_BALANCE_RESERVE = 0.50", source)
        self.assertIn("CUSTOM_SPLIT_CAP_DEFAULT_REPAIRED", source)
        self.assertIn("trade_skipped=false", source)

    def test_explicit_recovery_cap_is_never_overridden(self) -> None:
        source = CAP_AUTHORITY.read_text(encoding="utf-8")
        self.assertIn('"maximum_recovery_balance_fraction" in kwargs', source)
        self.assertIn('"minimum_balance_reserve" in kwargs', source)
        self.assertIn("return plan", source)

    def test_virtual_hook_waits_for_future_provider_tick_after_real_loss(self) -> None:
        source = VIRTUAL_AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("CUSTOM_VIRTUAL_AFTER_REAL_LOSS_BARRIER", source)
        self.assertIn("open_actual_closed=true", source)
        self.assertIn("same_settlement_tick_entry=false", source)
        self.assertIn("virtual_fast_path=false", source)
        self.assertIn("epoch <= int(barrier_epoch)", source)
        self.assertIn("normal_strategy_qualification_required=true", source)
        self.assertEqual(_tick_epoch({"epoch": 100}), 100)
        self.assertEqual(_tick_epoch({"tick": {"epoch": 101}}), 101)

    def test_final_worker_installs_cap_and_post_loss_barrier_last(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        consistency = source.index("install_custom_execution_consistency_authority()")
        equal_split = source.index("install_custom_split_equal_spread_authority()")
        cap_defaults = source.index("install_custom_split_cap_defaults_authority()")
        virtual_barrier = source.index("install_custom_virtual_post_loss_barrier_authority()")
        self.assertLess(consistency, equal_split)
        self.assertLess(equal_split, cap_defaults)
        self.assertLess(cap_defaults, virtual_barrier)
        self.assertIn("split_cap_defaults=canonical_10pct_and_0_50_reserve", source)
        self.assertIn("virtual_entry=real_position_settled_then_future_qualified_tick", source)

    def test_virtual_rows_remain_visible_but_are_kpi_neutral(self) -> None:
        source = KPI_JS.read_text(encoding="utf-8")
        self.assertIn("function isVirtual(row)", source)
        self.assertIn("allRows.filter((row) => !isVirtual(row))", source)
        self.assertIn('row.trade_kind || ""', source)
        self.assertIn("metrics.wins", source)
        self.assertIn("metrics.losses", source)
        self.assertIn("metrics.profit", source)
        self.assertNotIn("payload.trades =", source)

    def test_run_kpis_use_unbounded_server_aggregate_not_bounded_history_rows(self) -> None:
        source = KPI_JS.read_text(encoding="utf-8")
        self.assertIn("function summaryMetrics(me, payload)", source)
        self.assertIn("finiteMetric(summary.total)", source)
        self.assertIn("finiteMetric(summary.wins)", source)
        self.assertIn("finiteMetric(summary.losses)", source)
        self.assertIn("finiteMetric(summary.profit)", source)
        self.assertIn("localCutoff ? zeroMetrics() : rowFallbackMetrics(me, payload)", source)
        self.assertIn("ONLY KPI", source)
        self.assertIn("101, 1,000 or", source)
        self.assertIn("10,000 actual runs", source)
        self.assertIn("payloadCutoffTime(payload)", source)

        index = INDEX.read_text(encoding="utf-8")
        self.assertIn("virtual-kpi-neutrality.js?v=20260815-3", index)
        self.assertIn("netlify-realtime-client.js?v=20260815-3", index)


if __name__ == "__main__":
    unittest.main()
