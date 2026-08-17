from __future__ import annotations

from pathlib import Path
import unittest

from app.custom_split_cap_defaults_authority import _default_recovery_cap
from app.custom_virtual_post_loss_barrier_authority import _tick_epoch


ROOT = Path(__file__).resolve().parents[1]
CAP_AUTHORITY = ROOT / "app" / "custom_split_cap_defaults_authority.py"
VIRTUAL_AUTHORITY = ROOT / "app" / "custom_virtual_post_loss_barrier_authority.py"
WORKER = ROOT / "app" / "custom_strategy_worker.py"
LEGACY_KPI_JS = ROOT / "dashboard" / "virtual-kpi-neutrality.js"
FINAL_UI_JS = ROOT / "dashboard" / "final-ui-shell-v2.js"
INDEX = ROOT / "dashboard" / "index.html"


class PostLossSplitAndVirtualNeutralityTests(unittest.TestCase):
    def test_omitted_split_cap_uses_canonical_defaults_not_zero_percent(self) -> None:
        cap = _default_recovery_cap(current_balance=8274.93, base_stake=0.50)
        self.assertGreater(cap, 0.50)
        self.assertAlmostEqual(cap, 827.493, places=3)
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

    def test_historical_virtual_kpi_logic_remains_available_as_reference(self) -> None:
        source = LEGACY_KPI_JS.read_text(encoding="utf-8")
        self.assertIn("function isVirtual(row)", source)
        self.assertIn("allRows.filter((row) => !isVirtual(row))", source)
        self.assertIn('row.trade_kind || ""', source)
        self.assertNotIn("payload.trades =", source)

    def test_new_home_kpis_use_unbounded_server_summary_not_visible_rows(self) -> None:
        shell = FINAL_UI_JS.read_text(encoding="utf-8")
        index = INDEX.read_text(encoding="utf-8")
        metrics = shell.split("function metrics()", 1)[1].split("function home()", 1)[0]

        self.assertIn("const summary = state.trades?.summary || {}", metrics)
        self.assertIn("const meStats = state.me?.stats || {}", metrics)
        self.assertIn("summary.total ?? meStats.trades", metrics)
        self.assertIn("summary.wins ?? meStats.wins", metrics)
        self.assertIn("summary.losses ?? meStats.losses", metrics)
        self.assertIn("summary.profit ?? meStats.profit", metrics)
        self.assertNotIn("state.trades?.trades", metrics)
        # 6F-2 may request a large row window for the dedicated Run ledger, but
        # Home KPI values still come exclusively from the unbounded server summary.
        self.assertIn('json("/me/trades/today?limit=5000")', shell)

        self.assertNotIn("virtual-kpi-neutrality.js", index)
        self.assertNotIn("netlify-realtime-client.js", index)
        self.assertIn("vps-realtime-client-v2.js?v=20260817-6f2-1", index)
        self.assertIn("final-ui-shell-v2.js?v=20260817-6f2-1", index)
        self.assertNotIn("final-ui-shell-v1.js", index)


if __name__ == "__main__":
    unittest.main()
