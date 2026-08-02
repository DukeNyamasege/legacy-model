from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class StopHistoryContractTests(unittest.TestCase):
    def test_stop_resets_recovery_without_marking_history_reset(self) -> None:
        source = (ROOT / "app" / "lifecycle_reset_authority.py").read_text(
            encoding="utf-8"
        )
        stop_block = source.split('@app.post("/me/stop-trading")', 1)[1].split(
            '@app.post("/me/pause-trading")', 1
        )[0]
        self.assertIn("mark_history_reset=False", stop_block)
        self.assertIn('"history_preserved": True', stop_block)
        self.assertIn("Trade history is retained", stop_block)

    def test_only_explicit_clear_marks_history_reset(self) -> None:
        source = (ROOT / "app" / "lifecycle_reset_authority.py").read_text(
            encoding="utf-8"
        )
        clear_block = source.split('@app.post("/me/clear-trades")', 1)[1]
        self.assertIn("mark_history_reset=True", clear_block)
        start_block = source.split("def _start(", 1)[1].split(
            "def install_lifecycle_reset_authority", 1
        )[0]
        self.assertNotIn("_write_reset_marker", start_block)

    def test_daily_trade_stream_has_no_stop_session_filter(self) -> None:
        source = (ROOT / "app" / "final_personal_trade_stream.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_trade_belongs_to_session", source)
        self.assertNotIn("created <= reset_at", source)
        self.assertIn('"history_preserved_across_stop": True', source)
        self.assertIn("actual_rows = session.execute", source)


class CompactMobileUIContractTests(unittest.TestCase):
    def test_compact_mobile_css_is_installed_after_stable_dashboard(self) -> None:
        settings_guard = (ROOT / "app" / "dashboard_settings_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from app.mobile_compact_ui import install_mobile_compact_ui", settings_guard)
        self.assertIn("install_mobile_compact_ui(app)", settings_guard)

    def test_narrow_phone_typography_is_materially_smaller(self) -> None:
        source = (ROOT / "app" / "mobile_compact_ui.py").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 760px)", source)
        self.assertIn("@media (max-width: 420px)", source)
        self.assertIn("font-size: 9.5px !important", source)
        self.assertIn("font-size: 16px !important", source)
        self.assertIn("X-FOA-Mobile-UI-Version", source)


if __name__ == "__main__":
    unittest.main()
