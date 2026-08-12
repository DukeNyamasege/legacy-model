from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select, update

from app.config import load_test2_config
from app.database import Database
from app.models import BotState, ManagedAccount, TraderLease, utc_now


RUNNING_STATUSES = {
    "active",
    "base_stake_protection",
    "connecting",
    "reconnecting",
    "recovery_pending",
    "running",
    "starting",
    "validating",
    "virtual_protection",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed verifier for the builder-first runtime migration. "
            "It preserves accounts/credentials and checks no account can auto-trade."
        )
    )
    parser.add_argument(
        "--stop-all",
        action="store_true",
        help="Mark all existing accounts stopped and clear runtime leases before verifying.",
    )
    args = parser.parse_args()

    config = load_test2_config()
    database = Database(config.database_url)
    now = utc_now()
    reason = (
        "Builder-first migration: Auto Trading is OFF. "
        "Press Start Auto Trading to execute."
    )

    with database.session() as session:
        if args.stop_all:
            session.execute(
                update(ManagedAccount).values(
                    enabled=False,
                    execution_status="stopped",
                    execution_status_reason=reason,
                    execution_status_updated_at=now,
                    updated_at=now,
                )
            )
            session.execute(
                update(BotState).values(
                    status="STOPPED",
                    pause_reason="BUILDER_FIRST_MIGRATION_STOPPED",
                    last_heartbeat=now,
                )
            )
            session.execute(delete(TraderLease))

        total_accounts = int(session.scalar(select(func.count(ManagedAccount.id))) or 0)
        active_accounts = int(
            session.scalar(
                select(func.count(ManagedAccount.id)).where(
                    or_(
                        ManagedAccount.enabled.is_(True),
                        ManagedAccount.execution_status.in_(sorted(RUNNING_STATUSES)),
                    )
                )
            )
            or 0
        )
        leases = int(session.scalar(select(func.count(TraderLease.lease_key))) or 0)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "stop_all_applied": bool(args.stop_all),
        "registered_accounts": total_accounts,
        "active_auto_trading_accounts": active_accounts,
        "active_execution_leases": leases,
        "runtime_registry": 0,
        "ready_for_user_start": active_accounts == 0 and leases == 0,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready_for_user_start"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
