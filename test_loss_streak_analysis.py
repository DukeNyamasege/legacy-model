import unittest
from datetime import datetime, timedelta, timezone

from scripts.analyze_loss_streaks import TradeFact, build_report, find_loss_streaks


BASE_TIME = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def trade(
    sequence: int,
    outcome: str,
    market: str,
    *,
    efficiency: float = 0.82,
    impulse: float = 1.10,
    largest_move_ratio: float = 1.25,
) -> TradeFact:
    purchased = BASE_TIME + timedelta(seconds=sequence * 10)
    return TradeFact(
        signal_id=f"signal-{sequence}",
        contract_id=f"contract-{sequence}",
        market=market,
        outcome=outcome,
        profit=-0.50 if outcome == "LOSS" else 0.46,
        stake=0.50,
        payout=0.0 if outcome == "LOSS" else 0.96,
        duration_ticks=5,
        quality_score=8,
        efficiency=efficiency,
        impulse=impulse,
        largest_move_ratio=largest_move_ratio,
        movement_pattern="-----",
        purchase_time=purchased,
        settlement_time=purchased + timedelta(seconds=5),
    )


class LossStreakAnalysisTests(unittest.TestCase):
    def test_finds_only_consecutive_losses_at_or_above_threshold(self) -> None:
        rows = [
            trade(1, "WIN", "R_10"),
            trade(2, "LOSS", "R_10"),
            trade(3, "LOSS", "R_10"),
            trade(4, "LOSS", "R_75"),
            trade(5, "LOSS", "1HZ10V"),
            trade(6, "WIN", "R_100"),
            trade(7, "LOSS", "R_75"),
            trade(8, "LOSS", "R_100"),
        ]

        streaks = find_loss_streaks(rows, minimum_length=4)

        self.assertEqual(len(streaks), 1)
        self.assertEqual([row.signal_id for row in streaks[0]], [
            "signal-2",
            "signal-3",
            "signal-4",
            "signal-5",
        ])

    def test_report_exposes_repeated_market_and_feature_similarities(self) -> None:
        rows = [
            trade(1, "LOSS", "R_10", efficiency=0.80, impulse=1.00),
            trade(2, "LOSS", "R_10", efficiency=0.82, impulse=1.10),
            trade(3, "LOSS", "R_75", efficiency=0.84, impulse=1.20),
            trade(4, "LOSS", "1HZ10V", efficiency=0.86, impulse=1.30),
            trade(5, "WIN", "R_100"),
        ]

        report = build_report(
            rows,
            account="DOT***422",
            minimum_length=4,
            scope="all_runs",
        )

        self.assertEqual(report["qualifying_streaks"], 1)
        self.assertEqual(report["losses_inside_streaks"], 4)
        self.assertEqual(report["similarities"]["market_frequency"]["R_10"], 2)
        self.assertEqual(report["similarities"]["same_market_adjacent_losses"], 1)
        self.assertEqual(report["similarities"]["all_adjacent_loss_transitions"], 3)
        self.assertEqual(report["similarities"]["same_market_transition_rate"], 0.3333)
        self.assertEqual(report["similarities"]["average_efficiency"], 0.83)
        self.assertEqual(report["similarities"]["average_impulse"], 1.15)
        self.assertEqual(report["streaks"][0]["markets"], [
            "R_10",
            "R_10",
            "R_75",
            "1HZ10V",
        ])


if __name__ == "__main__":
    unittest.main()
