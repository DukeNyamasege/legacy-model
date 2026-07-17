from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.config import load_test2_config
from app.database import Database
from app.models import (
    CandidateSignalRecord,
    DirectionalSignal,
    ManagedAccount,
    ModelDecisionRecord,
    Trade,
)
from app.repositories.test2_repository import Test2Repository, mask_account_id
from app.token_store import decrypt_auth_payload


def enabled_managed_accounts(
    database: Database,
    encryption_key: str,
) -> list[tuple[str, bool, str, str]]:
    accounts: list[tuple[str, bool, str, str]] = []
    with database.session() as session:
        rows = session.scalars(select(ManagedAccount).order_by(ManagedAccount.id)).all()
        for row in rows:
            account_id = ""
            try:
                payload = decrypt_auth_payload(row.token_secret, encryption_key)
                account_id = str(payload.get("account_id", "")).strip()
            except Exception:
                account_id = "DECRYPT_FAILED"
            accounts.append(
                (
                    mask_account_id(account_id),
                    bool(row.enabled),
                    row.label,
                    str(row.execution_status),
                )
            )
    return accounts


def parse_utc_timestamp(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def classify_trade_group(
    rows: list[Trade],
    *,
    expected_accounts: set[str],
) -> dict[str, Any]:
    actual_accounts = {row.account_id_masked for row in rows}
    outcomes = {
        str(row.outcome or "").upper()
        for row in rows
        if str(row.outcome or "").upper() not in {"", "OPEN"}
    }
    open_contracts = sum(
        str(row.outcome or "").upper() in {"", "OPEN"} for row in rows
    )
    entry_ticks = {
        round(float(row.entry_tick), 8)
        for row in rows
        if row.entry_tick is not None
    }
    exit_ticks = {
        round(float(row.exit_tick), 8)
        for row in rows
        if row.exit_tick is not None
    }
    exit_digits = {row.exit_digit for row in rows if row.exit_digit is not None}
    participation_known = bool(expected_accounts)
    missing = sorted(expected_accounts - actual_accounts) if participation_known else []
    unexpected = sorted(actual_accounts - expected_accounts) if participation_known else []

    if open_contracts:
        status = "IN_PROGRESS"
    elif missing or unexpected:
        status = "PARTIAL_PURCHASE"
    elif len(outcomes) > 1:
        status = "OUTCOME_MISMATCH"
    elif len(entry_ticks) > 1 or len(exit_ticks) > 1 or len(exit_digits) > 1:
        status = "LIFECYCLE_VARIANCE"
    elif not participation_known:
        status = "HISTORICAL_UNSCOPED"
    else:
        status = "CONSISTENT"

    return {
        "status": status,
        "actual_accounts": actual_accounts,
        "outcomes": outcomes,
        "open_contracts": open_contracts,
        "entry_ticks": entry_ticks,
        "exit_ticks": exit_ticks,
        "exit_digits": exit_digits,
        "participation_known": participation_known,
        "missing": missing,
        "unexpected": unexpected,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--since",
        type=parse_utc_timestamp,
        help="Only include trades purchased at or after this ISO-8601 timestamp.",
    )
    scope.add_argument(
        "--since-minutes",
        type=float,
        help="Only include trades purchased during the most recent number of minutes.",
    )
    args = parser.parse_args()
    cutoff = args.since
    if args.since_minutes is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=args.since_minutes)

    config = load_test2_config(os.getenv("DERIV_BOT_CONFIG", ROOT / "config.yaml"))
    database = Database(config.database_url)
    repository = Test2Repository(database, config)

    print("=== SUMMARY ===")
    summary = repository.summary()
    print(summary)
    if cutoff is not None:
        print("audit_trade_cutoff=", cutoff.isoformat())

    managed = enabled_managed_accounts(database, config.deriv.token_encryption_key)
    enabled_masks = {
        account
        for account, enabled, _, status in managed
        if enabled and status == "active" and account != "DECRYPT_FAILED"
    }

    print("\n=== MANAGED ACCOUNTS ===")
    for item in managed:
        print(item)

    with database.session() as session:
        current_signal_ids = select(CandidateSignalRecord.signal_id).where(
            CandidateSignalRecord.run_id == repository.run_id
        )
        signals = {
            row.signal_id: row
            for row in session.scalars(
                select(CandidateSignalRecord).where(
                    CandidateSignalRecord.run_id == repository.run_id
                )
            ).all()
        }
        decisions = {
            row.signal_id: row
            for row in session.scalars(
                select(ModelDecisionRecord).where(
                    ModelDecisionRecord.signal_id.in_(current_signal_ids)
                )
            ).all()
        }
        directional_signals = {
            row.signal_id: row
            for row in session.scalars(
                select(DirectionalSignal).where(
                    DirectionalSignal.run_id == repository.run_id
                )
            ).all()
        }
        trade_query = (
            select(Trade)
            .where(Trade.signal_id.in_(current_signal_ids))
            .order_by(Trade.purchase_time.asc())
        )
        if cutoff is not None:
            trade_query = trade_query.where(Trade.purchase_time >= cutoff)
        trades = list(session.scalars(trade_query).all())

    groups: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        groups[trade.signal_id].append(trade)

    print("\n=== ACCOUNT TOTALS ===")
    by_account: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0}
    )
    for trade in trades:
        row = by_account[trade.account_id_masked]
        row["trades"] = int(row["trades"]) + 1
        row["wins"] = int(row["wins"]) + (
            1 if str(trade.outcome).upper() == "WIN" else 0
        )
        row["losses"] = int(row["losses"]) + (
            1 if str(trade.outcome).upper() == "LOSS" else 0
        )
        row["profit"] = float(row["profit"]) + float(trade.profit or 0.0)

    for account, row in sorted(by_account.items()):
        print(account, row)

    print("\n=== SIGNAL CONSISTENCY REPORT ===")
    critical_mismatch_count = 0
    lifecycle_variance_count = 0
    historical_unscoped_count = 0
    recent_groups = sorted(
        groups.items(),
        key=lambda item: max(t.purchase_time for t in item[1]),
        reverse=True,
    )[: max(1, args.limit)]

    for signal_id, rows in recent_groups:
        signal = signals.get(signal_id)
        decision = decisions.get(signal_id)
        directional = directional_signals.get(signal_id)
        expected_accounts = set(signal.expected_account_masks or []) if signal else set()
        registered_snapshot = (
            set(signal.registered_account_masks or []) if signal else set()
        )
        result = classify_trade_group(rows, expected_accounts=expected_accounts)
        profits = {round(float(row.profit or 0.0), 2) for row in rows}
        status = result["status"]
        if status in {"PARTIAL_PURCHASE", "OUTCOME_MISMATCH"}:
            critical_mismatch_count += 1
        elif status == "LIFECYCLE_VARIANCE":
            lifecycle_variance_count += 1
        elif status == "HISTORICAL_UNSCOPED":
            historical_unscoped_count += 1

        print(f"\n--- {status} {signal_id} ---")
        print("signal_status=", signal.final_status if signal else None)
        print("decision=", decision.final_decision if decision else None)
        print("trigger_digits=", signal.trigger_digits if signal else None)
        if directional is not None:
            print(
                "directional_contract=",
                directional.symbol,
                directional.direction,
                f"{directional.duration_ticks}t",
            )
        print(
            "rows=",
            len(rows),
            "expected_accounts_at_purchase=",
            len(expected_accounts) if result["participation_known"] else "UNKNOWN",
            "registered_snapshot=",
            len(registered_snapshot) if result["participation_known"] else "UNKNOWN",
            "missing=",
            result["missing"],
            "unexpected=",
            result["unexpected"],
        )
        print(
            "outcomes=",
            sorted(result["outcomes"]),
            "profits=",
            sorted(profits),
            "exit_digits=",
            sorted(str(value) for value in result["exit_digits"]),
            "open_contracts=",
            result["open_contracts"],
        )

        for trade in sorted(rows, key=lambda item: item.account_id_masked):
            print(
                f"  {trade.account_id_masked} contract={trade.contract_id} "
                f"outcome={trade.outcome} profit={trade.profit} "
                f"entry={trade.entry_tick} exit={trade.exit_tick} "
                f"exit_digit={trade.exit_digit} purchase={trade.purchase_time} "
                f"settle={trade.settlement_time}"
            )

    print("\n=== AUDIT COUNTS ===")
    print("critical_mismatches=", critical_mismatch_count)
    print("lifecycle_variances=", lifecycle_variance_count)
    print("historical_membership_unknown=", historical_unscoped_count)
    print("current_active_accounts=", len(enabled_masks))


if __name__ == "__main__":
    main()
