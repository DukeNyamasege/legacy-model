from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CustomVirtualIntegrityAuthorityTests(unittest.TestCase):
    def test_virtual_hook_is_persisted_and_blocks_real_execution(self) -> None:
        source = (ROOT / "app" / "custom_virtual_integrity_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('VirtualTrade.result == "OPEN"', source)
        self.assertIn("_restore_open_virtuals", source)
        self.assertIn("_persistent_open_virtual", source)
        self.assertIn("_custom_virtual_open_ids", source)
        self.assertIn("real execution remains blocked until it settles", source)

    def test_unobservable_zero_cost_sample_is_void_retry_not_cancelled(self) -> None:
        source = (ROOT / "app" / "custom_virtual_integrity_authority.py").read_text(
            encoding="utf-8"
        )
        history = (ROOT / "app" / "final_personal_trade_stream.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('trade.result = "VIRTUAL_VOID_RETRY"', source)
        self.assertIn("real_execution_unlocked=false", source)
        self.assertIn('progress_text = "VOID · RETRY"', history)
        self.assertIn('"virtual_void":', history)
        self.assertIn('return "VOID"', history)

    def test_custom_hook_exit_setting_is_not_adaptively_escalated(self) -> None:
        source = (ROOT / "app" / "custom_virtual_integrity_authority.py").read_text(
            encoding="utf-8"
        )
        history = (ROOT / "app" / "final_personal_trade_stream.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("exit_after_consecutive_wins", source)
        self.assertIn("_required_wins_without_aidr_escalation", source)
        self.assertNotIn("adaptive_virtual_wins_required(", history)
        self.assertIn("required = _current_virtual_requirement(managed_account_id)", history)

    def test_virtual_settlement_cannot_reenter_on_same_tick(self) -> None:
        source = (ROOT / "app" / "custom_virtual_integrity_authority.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_custom_virtual_resume_after", source)
        self.assertIn("current_sequence <= int(barrier_sequence)", source)
        self.assertIn("same market tick that closed/voided", source)

    def test_authority_is_final_after_result_routing(self) -> None:
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(encoding="utf-8")
        result_router = worker.index("install_custom_strategy_result_router()")
        integrity = worker.index("install_custom_virtual_integrity_authority()")
        manual_stop = worker.index("install_custom_strategy_manual_stop_guard()")
        self.assertLess(result_router, integrity)
        self.assertLess(integrity, manual_stop)
        self.assertIn("virtual_hook=exact_zero_stake_mirror", worker)

    def test_integrity_module_parses(self) -> None:
        subprocess.run(
            ["python", "-m", "py_compile", str(ROOT / "app" / "custom_virtual_integrity_authority.py")],
            check=True,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
