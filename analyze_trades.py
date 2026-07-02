import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "trading_bot.log"
STATE_PATH = ROOT / "bot_state.json"
OUT_DIR = ROOT / "analysis"


BUY_RE = re.compile(
    r"bought contract_id=(?P<contract_id>\d+)\s+stake=(?P<stake>[0-9.]+)\s+contract_type=(?P<contract_type>\S+)\s+barrier=(?P<barrier>\S+)\s+trigger=(?P<trigger>\S+)"
)
OUTCOME_RE = re.compile(r"(?P<kind>WIN|LOSS)\s+profit=(?P<profit>-?[0-9.]+)")
TOTAL_RE = re.compile(r"total_profit=(?P<total_profit>-?[0-9.]+)\s+profit_today=(?P<profit_today>-?[0-9.]+)\s+next_stake=(?P<next_stake>[0-9.]+)")


@dataclass
class TradeRow:
    contract_id: str
    token_tag: str
    entry_time: str = ""
    settle_time: str = ""
    contract_type: str = ""
    barrier: str = ""
    trigger: str = ""
    stake: float = 0.0
    outcome: str = ""
    profit: float = 0.0
    cumulative_profit: Optional[float] = None
    profit_today_after_trade: Optional[float] = None
    next_stake: Optional[float] = None

    def as_dict(self, sequence: int, cumulative_from_profit: float, drawdown: float) -> Dict[str, Any]:
        return {
            "sequence": sequence,
            "contract_id": self.contract_id,
            "token_tag": self.token_tag,
            "entry_time": self.entry_time,
            "settle_time": self.settle_time,
            "contract_type": self.contract_type,
            "barrier": self.barrier,
            "trigger": self.trigger,
            "stake": round(self.stake, 2),
            "outcome": self.outcome,
            "profit": round(self.profit, 2),
            "cumulative_profit": round(cumulative_from_profit, 2),
            "profit_today_after_trade": None if self.profit_today_after_trade is None else round(self.profit_today_after_trade, 2),
            "next_stake": None if self.next_stake is None else round(self.next_stake, 2),
            "drawdown_from_peak": round(drawdown, 2),
        }


def load_state() -> Dict[str, Any]:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def parse_log_lines() -> List[Dict[str, str]]:
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    parsed: List[Dict[str, str]] = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        row = {"time": parts[0], "level": parts[1], "message": parts[2]}
        for part in parts[3:]:
            if "=" in part:
                key, value = part.split("=", 1)
                row[key] = value
        parsed.append(row)
    return parsed


def build_trade_rows(parsed_lines: List[Dict[str, str]], token_tag: str) -> List[TradeRow]:
    trades: Dict[str, TradeRow] = {}
    last_contract_for_tag: Dict[str, str] = {}

    for row in parsed_lines:
        if row.get("token_tag") != token_tag:
            continue
        message = row.get("message", "")

        buy_match = BUY_RE.search(message)
        if buy_match:
            contract_id = buy_match.group("contract_id")
            trade = trades.setdefault(contract_id, TradeRow(contract_id=contract_id, token_tag=token_tag))
            trade.entry_time = row["time"]
            trade.contract_type = buy_match.group("contract_type")
            trade.barrier = buy_match.group("barrier")
            trade.trigger = buy_match.group("trigger")
            trade.stake = float(buy_match.group("stake"))
            last_contract_for_tag[token_tag] = contract_id
            continue

        outcome_match = OUTCOME_RE.search(message)
        if outcome_match:
            contract_id = row.get("contract_id", "")
            if not contract_id or contract_id == "-":
                contract_id = last_contract_for_tag.get(token_tag, "")
            if not contract_id:
                continue
            trade = trades.setdefault(contract_id, TradeRow(contract_id=contract_id, token_tag=token_tag))
            trade.settle_time = row["time"]
            trade.outcome = outcome_match.group("kind").lower()
            trade.profit = float(outcome_match.group("profit"))
            last_contract_for_tag[token_tag] = contract_id
            continue

        total_match = TOTAL_RE.search(message)
        if total_match:
            contract_id = row.get("contract_id", "")
            if not contract_id or contract_id == "-":
                contract_id = last_contract_for_tag.get(token_tag, "")
            if not contract_id:
                continue
            trade = trades.setdefault(contract_id, TradeRow(contract_id=contract_id, token_tag=token_tag))
            trade.cumulative_profit = float(total_match.group("total_profit"))
            trade.profit_today_after_trade = float(total_match.group("profit_today"))
            trade.next_stake = float(total_match.group("next_stake"))

    completed = [trade for trade in trades.values() if trade.outcome in {"win", "loss"}]
    completed.sort(key=lambda t: (t.settle_time, t.entry_time, int(t.contract_id)))
    return completed


def compute_streaks(trades: List[TradeRow]) -> Dict[str, Any]:
    longest = {"win": 0, "loss": 0}
    streak_runs: List[Dict[str, Any]] = []
    current_type = ""
    current_length = 0
    current_start = 0

    for index, trade in enumerate(trades, start=1):
        if trade.outcome != current_type:
            if current_type:
                streak_runs.append(
                    {
                        "outcome": current_type,
                        "length": current_length,
                        "start_sequence": current_start,
                        "end_sequence": index - 1,
                    }
                )
                longest[current_type] = max(longest[current_type], current_length)
            current_type = trade.outcome
            current_length = 1
            current_start = index
        else:
            current_length += 1

    if current_type:
        streak_runs.append(
            {
                "outcome": current_type,
                "length": current_length,
                "start_sequence": current_start,
                "end_sequence": len(trades),
            }
        )
        longest[current_type] = max(longest[current_type], current_length)

    return {
        "longest_win_streak": longest["win"],
        "longest_loss_streak": longest["loss"],
        "streak_runs": streak_runs,
    }


def compute_equity_curve(trades: List[TradeRow]) -> List[Dict[str, Any]]:
    curve: List[Dict[str, Any]] = []
    cumulative = 0.0
    peak = 0.0
    for index, trade in enumerate(trades, start=1):
        cumulative += trade.profit
        peak = max(peak, cumulative)
        curve.append(trade.as_dict(index, cumulative, peak - cumulative))
    return curve


def summarize(curve: List[Dict[str, Any]], trades: List[TradeRow], state_client: Dict[str, Any], streaks: Dict[str, Any]) -> Dict[str, Any]:
    profits = [row["profit"] for row in curve]
    wins = [row for row in curve if row["outcome"] == "win"]
    losses = [row for row in curve if row["outcome"] == "loss"]
    max_drawdown = max((row["drawdown_from_peak"] for row in curve), default=0.0)

    return {
        "trade_count": len(curve),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / len(curve)) * 100, 4) if curve else 0.0,
        "loss_rate": round((len(losses) / len(curve)) * 100, 4) if curve else 0.0,
        "stake": state_client.get("current_stake"),
        "profit_today": round(float(state_client.get("profit_today", 0.0)), 2),
        "total_profit": round(float(state_client.get("total_profit", 0.0)), 2),
        "average_profit_per_trade": round(sum(profits) / len(profits), 6) if profits else 0.0,
        "average_win": round(sum(row["profit"] for row in wins) / len(wins), 6) if wins else 0.0,
        "average_loss": round(sum(row["profit"] for row in losses) / len(losses), 6) if losses else 0.0,
        "best_trade_profit": max(profits) if profits else 0.0,
        "worst_trade_profit": min(profits) if profits else 0.0,
        "max_drawdown_from_peak": round(max_drawdown, 2),
        "first_trade_time": curve[0]["settle_time"] if curve else None,
        "last_trade_time": curve[-1]["settle_time"] if curve else None,
        "longest_win_streak": streaks["longest_win_streak"],
        "longest_loss_streak": streaks["longest_loss_streak"],
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    state = load_state()
    if not state.get("clients"):
        raise SystemExit("No clients found in bot_state.json")

    token_tag, client = next(iter(state["clients"].items()))
    parsed_lines = parse_log_lines()
    trades = build_trade_rows(parsed_lines, token_tag)
    curve = compute_equity_curve(trades)
    streaks = compute_streaks(trades)
    summary = summarize(curve, trades, client, streaks)

    OUT_DIR.mkdir(exist_ok=True)
    summary_path = OUT_DIR / "trade_summary.json"
    trade_csv_path = OUT_DIR / "trade_history.csv"
    curve_csv_path = OUT_DIR / "equity_curve.csv"
    streaks_csv_path = OUT_DIR / "streaks.csv"

    summary_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "state_snapshot": client,
                "streaks": streaks,
                "open_contracts_in_state": state.get("unresolved_contracts", []),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    write_csv(trade_csv_path, curve)
    write_csv(curve_csv_path, [{"sequence": row["sequence"], "settle_time": row["settle_time"], "cumulative_profit": row["cumulative_profit"], "drawdown_from_peak": row["drawdown_from_peak"]} for row in curve])
    write_csv(streaks_csv_path, streaks["streak_runs"])

    print(f"Wrote {len(curve)} completed trades to {trade_csv_path}")
    print(f"Summary: {summary_path}")
    print(f"Equity curve: {curve_csv_path}")
    print(f"Streak runs: {streaks_csv_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
