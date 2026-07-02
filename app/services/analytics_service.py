from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean

from sqlalchemy import select

from app.database import Database
from app.models import (
    CandidateSignalRecord,
    ModelDecisionRecord,
    ProposalRecord,
    TestRun,
    Trade,
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_test2(database: Database, run_name: str, export_directory: str | Path) -> dict:
    target = Path(export_directory)
    target.mkdir(parents=True, exist_ok=True)
    with database.session() as session:
        run = session.scalar(select(TestRun).where(TestRun.run_name == run_name))
        if run is None:
            raise ValueError(f"Run {run_name!r} does not exist")
        candidates = list(
            session.scalars(
                select(CandidateSignalRecord)
                .where(CandidateSignalRecord.run_id == run.id)
                .order_by(CandidateSignalRecord.generated_timestamp)
            ).all()
        )
        decisions = list(
            session.scalars(
                select(ModelDecisionRecord).order_by(ModelDecisionRecord.created_at)
            ).all()
        )
        proposals = {
            proposal.signal_id: proposal
            for proposal in session.scalars(select(ProposalRecord)).all()
        }
        trades = list(session.scalars(select(Trade).order_by(Trade.purchase_time)).all())

        candidate_rows = [
            {
                "signal_id": row.signal_id,
                "generated_at": row.generated_timestamp.isoformat(),
                "trigger_digits": "|".join(map(str, row.trigger_digits)),
                "signal_tick_epoch": row.signal_tick_epoch,
                "signal_tick_id": row.signal_tick_id,
                "consumed": row.consumed,
                "stale": row.stale,
                "final_status": row.final_status,
                "proposal_request_at": row.proposal_request_timestamp.isoformat()
                if row.proposal_request_timestamp
                else "",
                "proposal_response_at": row.proposal_response_timestamp.isoformat()
                if row.proposal_response_timestamp
                else "",
                "purchase_request_at": row.purchase_request_timestamp.isoformat()
                if row.purchase_request_timestamp
                else "",
                "purchase_confirmation_at": row.purchase_confirmation_timestamp.isoformat()
                if row.purchase_confirmation_timestamp
                else "",
                "ticks_between_signal_and_purchase": row.ticks_between_signal_and_purchase
                if row.ticks_between_signal_and_purchase is not None
                else "",
            }
            for row in candidates
        ]
        decision_rows = [
            {
                "decision_id": row.decision_id,
                "signal_id": row.signal_id,
                "hmm_state": row.hmm_output.get("state", ""),
                "hmm_ready": row.hmm_output.get("ready", False),
                "hmm_probabilities": json.dumps(
                    row.hmm_output.get("probabilities", {}), sort_keys=True
                ),
                "bayesian_ready": row.bayesian_output.get("ready", False),
                "posterior_mean": row.bayesian_output.get("posterior_mean", ""),
                "posterior_edge_probability": row.bayesian_output.get(
                    "probability_above_safety_threshold", ""
                ),
                "break_even_probability": row.break_even_rate,
                "expected_value": row.expected_value,
                "final_decision": row.final_decision,
                "rejection_reasons": "|".join(row.rejection_reasons),
            }
            for row in decisions
        ]

        cumulative = 0.0
        high_water = 0.0
        trade_rows = []
        equity_rows = []
        streak_rows = []
        current_streak_type = ""
        current_streak_start = 0
        current_streak_length = 0
        completed = [trade for trade in trades if trade.outcome in {"WIN", "LOSS"}]
        for index, trade in enumerate(completed, start=1):
            profit = float(trade.profit or 0.0)
            cumulative += profit
            high_water = max(high_water, cumulative)
            drawdown = high_water - cumulative
            proposal = proposals.get(trade.signal_id)
            trade_rows.append(
                {
                    "sequence": index,
                    "signal_id": trade.signal_id,
                    "contract_id": trade.contract_id,
                    "account": trade.account_id_masked,
                    "purchase_time": trade.purchase_time.isoformat(),
                    "settlement_time": trade.settlement_time.isoformat()
                    if trade.settlement_time
                    else "",
                    "entry_tick": trade.entry_tick if trade.entry_tick is not None else "",
                    "exit_tick": trade.exit_tick if trade.exit_tick is not None else "",
                    "exit_digit": trade.exit_digit if trade.exit_digit is not None else "",
                    "outcome": trade.outcome,
                    "profit": profit,
                    "cumulative_profit": cumulative,
                    "drawdown": drawdown,
                    "aligned_with_signal": trade.aligned_with_signal,
                    "proposal_id": proposal.proposal_id if proposal else "",
                    "break_even_probability": proposal.break_even_probability
                    if proposal
                    else "",
                    "predicted_win_probability": proposal.predicted_win_probability
                    if proposal
                    else "",
                    "expected_value": proposal.expected_value if proposal else "",
                }
            )
            equity_rows.append(
                {
                    "sequence": index,
                    "settlement_time": trade.settlement_time.isoformat()
                    if trade.settlement_time
                    else "",
                    "profit": profit,
                    "cumulative_profit": cumulative,
                    "high_water_mark": high_water,
                    "drawdown": drawdown,
                }
            )
            if trade.outcome != current_streak_type:
                if current_streak_length:
                    start_trade = completed[current_streak_start]
                    end_trade = completed[index - 2]
                    streak_rows.append(
                        {
                            "type": current_streak_type,
                            "length": current_streak_length,
                            "start_trade": start_trade.contract_id,
                            "end_trade": end_trade.contract_id,
                            "start_time": start_trade.settlement_time.isoformat(),
                            "end_time": end_trade.settlement_time.isoformat(),
                        }
                    )
                current_streak_type = trade.outcome
                current_streak_start = index - 1
                current_streak_length = 1
            else:
                current_streak_length += 1
        if current_streak_length:
            start_trade = completed[current_streak_start]
            end_trade = completed[-1]
            streak_rows.append(
                {
                    "type": current_streak_type,
                    "length": current_streak_length,
                    "start_trade": start_trade.contract_id,
                    "end_trade": end_trade.contract_id,
                    "start_time": start_trade.settlement_time.isoformat(),
                    "end_time": end_trade.settlement_time.isoformat(),
                }
            )

    _write_csv(
        target / "candidate_signals.csv",
        list(candidate_rows[0].keys())
        if candidate_rows
        else [
            "signal_id",
            "generated_at",
            "trigger_digits",
            "signal_tick_epoch",
            "signal_tick_id",
            "consumed",
            "stale",
            "final_status",
            "proposal_request_at",
            "proposal_response_at",
            "purchase_request_at",
            "purchase_confirmation_at",
            "ticks_between_signal_and_purchase",
        ],
        candidate_rows,
    )
    _write_csv(
        target / "model_decisions.csv",
        list(decision_rows[0].keys())
        if decision_rows
        else [
            "decision_id",
            "signal_id",
            "hmm_state",
            "hmm_ready",
            "hmm_probabilities",
            "bayesian_ready",
            "posterior_mean",
            "posterior_edge_probability",
            "break_even_probability",
            "expected_value",
            "final_decision",
            "rejection_reasons",
        ],
        decision_rows,
    )
    trade_fields = [
        "sequence",
        "signal_id",
        "contract_id",
        "account",
        "purchase_time",
        "settlement_time",
        "entry_tick",
        "exit_tick",
        "exit_digit",
        "outcome",
        "profit",
        "cumulative_profit",
        "drawdown",
        "aligned_with_signal",
        "proposal_id",
        "break_even_probability",
        "predicted_win_probability",
        "expected_value",
    ]
    _write_csv(target / "trade_history.csv", trade_fields, trade_rows)
    _write_csv(
        target / "equity_curve.csv",
        [
            "sequence",
            "settlement_time",
            "profit",
            "cumulative_profit",
            "high_water_mark",
            "drawdown",
        ],
        equity_rows,
    )
    _write_csv(
        target / "streaks.csv",
        ["type", "length", "start_trade", "end_trade", "start_time", "end_time"],
        streak_rows,
    )

    wins = [row for row in trade_rows if row["outcome"] == "WIN"]
    losses = [row for row in trade_rows if row["outcome"] == "LOSS"]
    gross_profit = sum(row["profit"] for row in wins)
    gross_loss = abs(sum(row["profit"] for row in losses))
    win_streaks = [row["length"] for row in streak_rows if row["type"] == "WIN"]
    loss_streaks = [row["length"] for row in streak_rows if row["type"] == "LOSS"]
    proposal_values = list(proposals.values())
    summary = {
        "run_id": run_name,
        "total_candidate_signals": len(candidate_rows),
        "purchased_trades": len(trade_rows),
        "skipped_signals": sum(row["final_status"].startswith("SKIP") for row in candidate_rows),
        "skip_reasons": dict(
            Counter(
                row["final_status"]
                for row in candidate_rows
                if row["final_status"].startswith("SKIP")
            )
        ),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trade_rows) if trade_rows else 0.0,
        "average_break_even_win_rate": mean(
            proposal.break_even_probability for proposal in proposal_values
        )
        if proposal_values
        else 0.0,
        "net_profit": sum(row["profit"] for row in trade_rows),
        "average_win": mean(row["profit"] for row in wins) if wins else 0.0,
        "average_loss": mean(row["profit"] for row in losses) if losses else 0.0,
        "expectancy": mean(row["profit"] for row in trade_rows) if trade_rows else 0.0,
        "payoff_ratio": (
            mean(row["profit"] for row in wins)
            / abs(mean(row["profit"] for row in losses))
            if wins and losses
            else 0.0
        ),
        "profit_factor": gross_profit / gross_loss if gross_loss else 0.0,
        "maximum_drawdown": max((row["drawdown"] for row in equity_rows), default=0.0),
        "longest_winning_streak": max(win_streaks, default=0),
        "longest_losing_streak": max(loss_streaks, default=0),
        "average_winning_streak": mean(win_streaks) if win_streaks else 0.0,
        "average_losing_streak": mean(loss_streaks) if loss_streaks else 0.0,
        "rolling_50_win_rate": (
            sum(row["outcome"] == "WIN" for row in trade_rows[-50:])
            / len(trade_rows[-50:])
            if trade_rows
            else 0.0
        ),
        "rolling_50_expectancy": (
            mean(row["profit"] for row in trade_rows[-50:]) if trade_rows else 0.0
        ),
        "correctly_aligned_percentage": (
            sum(bool(row["aligned_with_signal"]) for row in trade_rows) / len(trade_rows)
            if trade_rows
            else 0.0
        ),
    }
    (target / "trade_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary
