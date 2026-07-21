from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_test2_config
from app.database import Database
from app.models import CandidateSignalRecord, DirectionalSignal, Trade
from app.repositories.test2_repository import Test2Repository, mask_account_id


@dataclass(frozen=True, slots=True)
class TradeFact:
    signal_id: str
    contract_id: str
    market: str
    outcome: str
    profit: float
    stake: float
    payout: float
    duration_ticks: int
    quality_score: int | None
    efficiency: float | None
    impulse: float | None
    largest_move_ratio: float | None
    movement_pattern: str
    purchase_time: datetime
    settlement_time: datetime


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _movement_pattern(values: list[Any]) -> str:
    pattern: list[str] = []
    for value in values:
        parsed = _optional_float(value)
        pattern.append(
            "+" if parsed and parsed > 0 else "-" if parsed and parsed < 0 else "0"
        )
    return "".join(pattern) or "unknown"


def find_loss_streaks(
    rows: list[TradeFact],
    *,
    minimum_length: int,
) -> list[list[TradeFact]]:
    streaks: list[list[TradeFact]] = []
    current: list[TradeFact] = []
    for row in rows:
        if row.outcome == "LOSS":
            current.append(row)
            continue
        if len(current) >= minimum_length:
            streaks.append(current)
        current = []
    if len(current) >= minimum_length:
        streaks.append(current)
    return streaks


def _average(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(mean(present), 4) if present else None


def build_report(
    rows: list[TradeFact],
    *,
    account: str,
    minimum_length: int,
    scope: str,
) -> dict[str, Any]:
    streaks = find_loss_streaks(rows, minimum_length=minimum_length)
    losses = [trade for streak in streaks for trade in streak]
    market_counts = Counter(trade.market for trade in losses)
    duration_counts = Counter(f"{trade.duration_ticks}t" for trade in losses)
    score_counts = Counter(str(trade.quality_score) for trade in losses)
    pattern_counts = Counter(trade.movement_pattern for trade in losses)
    repeated_transitions = sum(
        previous.market == current.market
        for streak in streaks
        for previous, current in zip(streak[:-1], streak[1:])
    )
    total_transitions = sum(max(0, len(streak) - 1) for streak in streaks)

    return {
        "account": account,
        "scope": scope,
        "minimum_loss_streak": minimum_length,
        "settled_trades_analyzed": len(rows),
        "qualifying_streaks": len(streaks),
        "losses_inside_streaks": len(losses),
        "similarities": {
            "market_frequency": dict(market_counts.most_common()),
            "duration_frequency": dict(duration_counts.most_common()),
            "quality_score_frequency": dict(score_counts.most_common()),
            "movement_pattern_frequency": dict(pattern_counts.most_common()),
            "same_market_adjacent_losses": repeated_transitions,
            "all_adjacent_loss_transitions": total_transitions,
            "same_market_transition_rate": round(
                repeated_transitions / total_transitions,
                4,
            ) if total_transitions else 0.0,
            "single_market_streaks": sum(
                len({trade.market for trade in streak}) == 1 for streak in streaks
            ),
            "average_efficiency": _average([trade.efficiency for trade in losses]),
            "average_impulse": _average([trade.impulse for trade in losses]),
            "average_largest_move_ratio": _average(
                [trade.largest_move_ratio for trade in losses]
            ),
            "total_profit": round(sum(trade.profit for trade in losses), 2),
            "total_stake": round(sum(trade.stake for trade in losses), 2),
        },
        "streaks": [
            {
                "length": len(streak),
                "started": streak[0].settlement_time.isoformat(),
                "ended": streak[-1].settlement_time.isoformat(),
                "markets": [trade.market for trade in streak],
                "total_profit": round(sum(trade.profit for trade in streak), 2),
                "trades": [
                    {
                        "signal_id": trade.signal_id,
                        "contract_id": trade.contract_id,
                        "market": trade.market,
                        "stake": trade.stake,
                        "payout": trade.payout,
                        "duration": f"{trade.duration_ticks}t",
                        "quality_score": trade.quality_score,
                        "efficiency": trade.efficiency,
                        "impulse": trade.impulse,
                        "largest_move_ratio": trade.largest_move_ratio,
                        "movement_pattern": trade.movement_pattern,
                        "profit": trade.profit,
                        "purchased": trade.purchase_time.isoformat(),
                        "settled": trade.settlement_time.isoformat(),
                    }
                    for trade in streak
                ],
            }
            for streak in streaks
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze repeated master-account loss streaks without exposing tokens.",
    )
    parser.add_argument(
        "--account",
        default=os.getenv("COPYTRADING_MASTER_ACCOUNT_ID", ""),
        help="Raw or masked master account ID, for example DOT***422.",
    )
    parser.add_argument("--minimum-length", type=int, default=4)
    parser.add_argument("--all-runs", action="store_true")
    args = parser.parse_args()

    if not args.account.strip():
        parser.error("--account is required when COPYTRADING_MASTER_ACCOUNT_ID is unset")
    minimum_length = max(2, int(args.minimum_length))
    requested_account = args.account.strip()
    account_mask = (
        requested_account
        if "***" in requested_account
        else mask_account_id(requested_account)
    )

    config = load_test2_config(os.getenv("DERIV_BOT_CONFIG", ROOT / "config.yaml"))
    database = Database(config.database_url)
    repository = Test2Repository(database, config)

    with database.session() as session:
        query = (
            select(Trade, CandidateSignalRecord, DirectionalSignal)
            .join(
                CandidateSignalRecord,
                CandidateSignalRecord.signal_id == Trade.signal_id,
            )
            .outerjoin(
                DirectionalSignal,
                DirectionalSignal.signal_id == Trade.signal_id,
            )
            .where(
                Trade.account_id_masked == account_mask,
                Trade.settlement_time.is_not(None),
                func.upper(Trade.outcome).in_(("WIN", "LOSS")),
            )
            .order_by(Trade.settlement_time.asc(), Trade.id.asc())
        )
        if not args.all_runs:
            query = query.where(CandidateSignalRecord.run_id == repository.run_id)
        records = session.execute(query).all()

    facts: list[TradeFact] = []
    for trade, candidate, directional in records:
        features = dict(directional.feature_values or {}) if directional else {}
        movements = list(directional.movements or []) if directional else []
        facts.append(
            TradeFact(
                signal_id=trade.signal_id,
                contract_id=trade.contract_id,
                market=str(directional.symbol if directional else candidate.symbol),
                outcome=str(trade.outcome or "").upper(),
                profit=float(trade.profit or 0.0),
                stake=float(trade.buy_price or 0.0),
                payout=float(trade.payout or 0.0),
                duration_ticks=int(
                    (
                        directional.duration_ticks
                        if directional
                        else trade.contract_duration
                    )
                    or 0
                ),
                quality_score=(
                    int(directional.quality_score) if directional else None
                ),
                efficiency=_optional_float(features.get("efficiency")),
                impulse=_optional_float(features.get("impulse")),
                largest_move_ratio=_optional_float(
                    features.get("largest_move_ratio")
                ),
                movement_pattern=_movement_pattern(movements),
                purchase_time=trade.purchase_time,
                settlement_time=trade.settlement_time,
            )
        )

    report = build_report(
        facts,
        account=account_mask,
        minimum_length=minimum_length,
        scope="all_runs" if args.all_runs else f"run_id:{repository.run_id}",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
