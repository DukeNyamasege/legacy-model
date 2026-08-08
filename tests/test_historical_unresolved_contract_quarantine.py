from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

from app.historical_unresolved_contract_quarantine import (
    STALE_TICK_CONTRACT_AGE_SECONDS,
    stale_tick_contract_requires_manual_review,
)


ROOT = Path(__file__).resolve().parents[1]


def trade(*, age_seconds: int, unit: str = "t", settled: bool = False):
    now = datetime(2026, 8, 8, 7, 30, tzinfo=timezone.utc)
    return SimpleNamespace(
        settlement_time=now if settled else None,
        contract_duration_unit=unit,
        provider_purchase_time=now - timedelta(seconds=age_seconds),
        provider_start_time=None,
        purchase_time=now - timedelta(seconds=age_seconds),
    ), now


class HistoricalUnresolvedContractQuarantineTests(unittest.TestCase):
    def test_old_tick_contract_moves_to_manual_review(self) -> None:
        row, now = trade(age_seconds=STALE_TICK_CONTRACT_AGE_SECONDS + 1)
        self.assertTrue(stale_tick_contract_requires_manual_review(row, now=now))

    def test_recent_tick_contract_remains_live_for_reconciliation(self) -> None:
        row, now = trade(age_seconds=5 * 60)
        self.assertFalse(stale_tick_contract_requires_manual_review(row, now=now))

    def test_non_tick_contract_is_never_auto_quarantined(self) -> None:
        row, now = trade(age_seconds=24 * 60 * 60, unit="s")
        self.assertFalse(stale_tick_contract_requires_manual_review(row, now=now))

    def test_settled_contract_is_never_quarantined(self) -> None:
        row, now = trade(
            age_seconds=STALE_TICK_CONTRACT_AGE_SECONDS + 1,
            settled=True,
        )
        self.assertFalse(stale_tick_contract_requires_manual_review(row, now=now))

    def test_worker_installs_quarantine_before_live_execution(self) -> None:
        source = (ROOT / "app" / "production_worker_integration.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_historical_unresolved_contract_quarantine", source)
        self.assertLess(
            source.index("install_historical_unresolved_contract_quarantine()"),
            source.index("install_final_multi_strategy_execution()"),
        )

    def test_quarantine_preserves_financial_result(self) -> None:
        source = (
            ROOT / "app" / "historical_unresolved_contract_quarantine.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Trade.requires_manual_review.is_(False)", source)
        self.assertIn("trade.requires_manual_review = True", source)
        self.assertNotIn("trade.profit =", source)
        self.assertNotIn("trade.outcome =", source)
        self.assertNotIn("trade.settlement_time =", source)
        self.assertIn("financial_outcome_assumed=false", source)


if __name__ == "__main__":
    unittest.main()
