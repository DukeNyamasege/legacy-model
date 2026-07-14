from __future__ import annotations

import argparse
import os
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.config import load_test2_config
from app.database import Database
from app.models import CandidateSignalRecord, ManagedAccount, ModelDecisionRecord, Trade
from app.repositories.test2_repository import Test2Repository, mask_account_id
from app.token_store import decrypt_auth_payload


ROOT = Path(__file__).resolve().parents[1]


def enabled_managed_accounts(
    database: Database,
    encryption_key: str,
) -> list[tuple[str, bool, str]]:
    accounts: list[tuple[str, bool, str]] = []
    with database.session() as session:
        rows = session.scalars(select(ManagedAccount).order_by(ManagedAccount.id)).all()
        for row in rows:
            account_id = ""
            try:
                payload = decrypt_auth_payload(row.token_secret, encryption_key)
                account_id = str(payload.get("account_id", "")).strip()
            except Exception:
                account_id = "DECRYPT_FAILED"
            accounts.append((mask_account_id(account_id), bool(row.enabled), row.label))
    return accounts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    config = load_test2_config(os.getenv("DERIV_BOT_CONFIG", ROOT / "config.yaml"))
    database = Database(config.database_url)
    repository = Test2Repository(database, config)

    print("=== SUMMARY ===")
    print(repository.summary())

    managed = enabled_managed_accounts(database, config.deriv.token_encryption_key)
    enabled_masks = {
        account
        for account, enabled, _ in managed
        if enabled and account != "DECRYPT_FAILED"
    }

    print("\n=== MANAGED ACCOUNTS ===")
    for item in managed:
        print(item)

    with database.session() as session:
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
                    ModelDecisionRecord.signal_id.in_(
                        select(CandidateSignalRecord.signal_id).where(
                            CandidateSignalRecord.run_id == repository.run_id
                        )
                    )
                )
            ).all()
        }
        trades = list(
            session.scalars(
                select(Trade)
                .where(
                    Trade.signal_id.in_(
                        select(CandidateSignalRecord.signal_id).where(
                            CandidateSignalRecord.run_id == repository.run_id
                        )
                    )
                )
                .order_by(Trade.purchase_time.asc())
            ).all()
        )

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
    mismatch_count = 0
    recent_groups = sorted(
        groups.items(),
        key=lambda item: max(t.purchase_time for t in item[1]),
        reverse=True,
    )[: max(1, args.limit)]

    for signal_id, rows in recent_groups:
        signal = signals.get(signal_id)
        decision = decisions.get(signal_id)
        accounts = {row.account_id_masked for row in rows}
        outcomes = {str(row.outcome).upper() for row in rows}
        profits = {round(float(row.profit or 0.0), 2) for row in rows}
        exit_digits = {row.exit_digit for row in rows}
        missing = sorted(enabled_masks - accounts)
        ok = (
            len(rows) == len(enabled_masks)
            and not missing
            and len(outcomes) == 1
            and len(profits) == 1
            and len(exit_digits) == 1
        )
        if not ok:
            mismatch_count += 1

        print(f"\n--- {'OK' if ok else 'MISMATCH'} {signal_id} ---")
        print("signal_status=", signal.final_status if signal else None)
        print("decision=", decision.final_decision if decision else None)
        print("trigger_digits=", signal.trigger_digits if signal else None)
        print(
            "rows=",
            len(rows),
            "expected_accounts=",
            len(enabled_masks),
            "missing=",
            missing,
        )
        print(
            "outcomes=",
            sorted(outcomes),
            "profits=",
            sorted(profits),
            "exit_digits=",
            sorted(str(value) for value in exit_digits),
        )

        for trade in sorted(rows, key=lambda item: item.account_id_masked):
            print(
                f"  {trade.account_id_masked} contract={trade.contract_id} "
                f"outcome={trade.outcome} profit={trade.profit} "
                f"entry={trade.entry_tick} exit={trade.exit_tick} "
                f"exit_digit={trade.exit_digit} purchase={trade.purchase_time} "
                f"settle={trade.settlement_time}"
            )

    print("\n=== MISMATCH COUNT ===")
    print(mismatch_count)


if __name__ == "__main__":
    main()
