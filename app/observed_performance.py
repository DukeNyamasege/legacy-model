"""Observed Martingale accounting from matching real execution trajectories.

This module deliberately contains no stake simulation.  It groups actual,
settled account contracts by their complete ordered Deriv economics and selects
the largest matching account cohort as the observed system path.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Hashable, Iterable


def _cents(value: float) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class ObservedExecution:
    account_id: Hashable
    trade_id: int
    signal_id: str
    symbol: str
    purchased_at: datetime
    buy_price: float
    payout: float
    profit: float
    outcome: str

    @property
    def economics(self) -> tuple[str, str, str, str, str]:
        return (
            self.signal_id,
            _cents(self.buy_price),
            _cents(self.payout),
            _cents(self.profit),
            self.outcome.upper(),
        )


def observed_martingale_cohort(
    executions: Iterable[ObservedExecution],
) -> dict[str, object]:
    by_account: dict[Hashable, list[ObservedExecution]] = defaultdict(list)
    all_executions = list(executions)
    for execution in all_executions:
        by_account[execution.account_id].append(execution)
    for sequence in by_account.values():
        sequence.sort(key=lambda row: (row.purchased_at, row.trade_id))

    grouped: dict[tuple[tuple[str, str, str, str, str], ...], list[Hashable]] = defaultdict(list)
    for account_id, sequence in by_account.items():
        grouped[tuple(row.economics for row in sequence)].append(account_id)

    if not grouped:
        return {
            "observed_martingale_pnl": 0.0,
            "observed_maximum_stake": 0.0,
            "observed_martingale_stake_volume": 0.0,
            "observed_current_drawdown": 0.0,
            "observed_max_drawdown": 0.0,
            "martingale_cohort_size": 0,
            "martingale_population": 0,
            "martingale_cohort_confidence": 0.0,
            "martingale_cohort_trade_count": 0,
            "martingale_cohort_status": "NO_OBSERVED_EXECUTION",
            "martingale_cohort_sample_sufficient": False,
            "martingale_dominant_signature": "",
            "dominant_cohort_account_ids": (),
            "representative_account_id": None,
            "per_signal_consistency": [],
        }

    # Largest identical population wins. Coverage and signature hash provide a
    # stable tie-break, never balance, profit, leader, or callback ordering.
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            -len(item[1]),
            -len(item[0]),
            hashlib.sha256(repr(item[0]).encode()).hexdigest(),
        ),
    )
    signature, cohort_accounts = ranked[0]
    representative_id = sorted(cohort_accounts, key=lambda value: str(value))[0]
    representative = by_account[representative_id]
    pnl = sum(row.profit for row in representative)
    stake_volume = sum(row.buy_price for row in representative)
    maximum_stake = max((row.buy_price for row in representative), default=0.0)
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in representative:
        cumulative += row.profit
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    current_drawdown = peak - cumulative
    population = len(by_account)
    cohort_size = len(cohort_accounts)
    confidence = cohort_size / population if population else 0.0
    sample_sufficient = cohort_size >= 2
    status = (
        "LOW_SAMPLE_EXECUTION"
        if not sample_sufficient
        else "OBSERVED_CONSISTENT"
        if cohort_size == population
        else "OBSERVED_DOMINANT_COHORT"
    )

    per_signal: list[dict[str, object]] = []
    signal_rows: dict[str, list[ObservedExecution]] = defaultdict(list)
    for row in all_executions:
        signal_rows[row.signal_id].append(row)
    for signal_id, rows in sorted(signal_rows.items()):
        economic_groups: dict[tuple[str, str, str, str], int] = defaultdict(int)
        for row in rows:
            economic_groups[row.economics[1:]] += 1
        dominant_economics, dominant_count = sorted(
            economic_groups.items(), key=lambda item: (-item[1], repr(item[0]))
        )[0]
        per_signal.append(
            {
                "signal_id": signal_id,
                "market": sorted({row.symbol for row in rows})[0],
                "total": len(rows),
                "dominant_count": dominant_count,
                "different_count": len(rows) - dominant_count,
                "consistency_pct": round(dominant_count / len(rows) * 100.0, 2),
                "dominant_stake": float(dominant_economics[0]),
                "dominant_payout": float(dominant_economics[1]),
                "dominant_profit": float(dominant_economics[2]),
                "dominant_outcome": dominant_economics[3],
            }
        )

    signature_hash = hashlib.sha256(
        json.dumps(signature, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return {
        "observed_martingale_pnl": round(pnl, 2),
        "observed_maximum_stake": round(maximum_stake, 2),
        "observed_martingale_stake_volume": round(stake_volume, 2),
        "observed_current_drawdown": round(current_drawdown, 2),
        "observed_max_drawdown": round(max_drawdown, 2),
        "martingale_cohort_size": cohort_size,
        "martingale_population": population,
        "martingale_cohort_confidence": round(confidence, 4),
        "martingale_cohort_trade_count": len(representative),
        "martingale_cohort_status": status,
        "martingale_cohort_sample_sufficient": sample_sufficient,
        "martingale_dominant_signature": signature_hash,
        "dominant_cohort_account_ids": tuple(
            sorted(cohort_accounts, key=lambda value: str(value))
        ),
        "representative_account_id": representative_id,
        "per_signal_consistency": per_signal,
    }
