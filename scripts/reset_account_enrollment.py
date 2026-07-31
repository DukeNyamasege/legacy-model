#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from app.account_reenrollment import (  # noqa: E402
    ACCOUNT_ENROLLMENTS,
    ACCOUNT_ENROLLMENT_GENERATION_KEY,
    ACCOUNT_ENROLLMENT_RESET_COMMIT_KEY,
)
from app.config import load_test2_config  # noqa: E402
from app.database import Database  # noqa: E402
from app.models import (  # noqa: E402
    AuditEvent,
    Base,
    ClientSession,
    ManagedAccount,
    OAuthLoginState,
    RuntimePreference,
    Trade,
    TraderLease,
    utc_now,
)


CONFIRMATION = "RESET_ALL_AUTO_TRADERS"
ARCHIVED_STATUS = "archived_rejoin_required"
ARCHIVED_REASON = (
    "Service enrollment was reset. Log in again and explicitly start auto trading."
)


def _set_preference(session, key: str, value: str) -> None:
    row = session.get(RuntimePreference, key, with_for_update=True)
    if row is None:
        row = RuntimePreference(preference_key=key)
        session.add(row)
    row.preference_value = str(value)
    row.updated_at = utc_now()


def _preference(session, key: str) -> str:
    row = session.get(RuntimePreference, key, with_for_update=True)
    return str(row.preference_value if row else "")


def _integer_preference(session, key: str) -> int:
    try:
        return max(0, int(_preference(session, key) or 0))
    except (TypeError, ValueError):
        return 0


def _count(session, model, *conditions: Any) -> int:
    statement = select(func.count()).select_from(model)
    if conditions:
        statement = statement.where(*conditions)
    return int(session.scalar(statement) or 0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Archive every existing auto-trading registration and start a new "
            "enrollment generation without deleting historical trades."
        )
    )
    parser.add_argument("--confirm", default="")
    parser.add_argument("--deployment-id", default="manual")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-open-trades",
        action="store_true",
        help="Proceed even when actual provider trade rows are still OPEN.",
    )
    args = parser.parse_args()

    if not args.dry_run and args.confirm != CONFIRMATION:
        raise SystemExit(
            f"Refusing account reset. Pass --confirm {CONFIRMATION} exactly."
        )

    config = load_test2_config(
        os.getenv("DERIV_BOT_CONFIG", str(ROOT / "config.yaml"))
    )
    database = Database(config.database_url)

    # The API normally creates this table at startup. The deployment reset runs
    # before API startup, so create only missing metadata objects here as well.
    Base.metadata.create_all(database.engine)

    deployment_id = str(args.deployment_id or "manual").strip()[:100]
    with database.session() as session:
        total_accounts = _count(session, ManagedAccount)
        enabled_accounts = _count(
            session,
            ManagedAccount,
            ManagedAccount.enabled.is_(True),
        )
        active_sessions = _count(session, ClientSession)
        open_trades = _count(session, Trade, Trade.outcome == "OPEN")
        current_generation = _integer_preference(
            session,
            ACCOUNT_ENROLLMENT_GENERATION_KEY,
        )
        previous_reset_commit = _preference(
            session,
            ACCOUNT_ENROLLMENT_RESET_COMMIT_KEY,
        )

        preview = {
            "status": "dry_run" if args.dry_run else "pending",
            "deployment_id": deployment_id,
            "current_generation": current_generation,
            "next_generation": current_generation + 1,
            "registered_accounts_to_archive": total_accounts,
            "enabled_accounts_to_stop": enabled_accounts,
            "browser_sessions_to_invalidate": active_sessions,
            "open_actual_trades": open_trades,
            "historical_trades_preserved": True,
        }

        if args.dry_run:
            print(json.dumps(preview, indent=2, sort_keys=True))
            return 0

        if previous_reset_commit and previous_reset_commit == deployment_id:
            preview.update(
                {
                    "status": "already_applied",
                    "next_generation": current_generation,
                    "registered_accounts_to_archive": 0,
                    "enabled_accounts_to_stop": 0,
                    "browser_sessions_to_invalidate": 0,
                }
            )
            print(json.dumps(preview, indent=2, sort_keys=True))
            return 0

        if open_trades and not args.allow_open_trades:
            raise SystemExit(
                "Refusing to reset while actual provider trades are OPEN. "
                "Reconcile or settle them first; historical rows will not be guessed."
            )

        reset_at = datetime.now(timezone.utc)
        next_generation = current_generation + 1

        # No old registration can auto-start after this transaction. Credentials
        # remain encrypted in historical rows solely so prior account relationships
        # and audit evidence are preserved in the database backup/history.
        session.execute(
            update(ManagedAccount).values(
                enabled=False,
                execution_status=ARCHIVED_STATUS,
                execution_status_reason=ARCHIVED_REASON,
                execution_status_updated_at=reset_at,
                updated_at=reset_at,
            )
        )
        invalidated_sessions = session.execute(delete(ClientSession)).rowcount or 0
        discarded_oauth_states = session.execute(delete(OAuthLoginState)).rowcount or 0
        cleared_leases = session.execute(delete(TraderLease)).rowcount or 0

        _set_preference(
            session,
            ACCOUNT_ENROLLMENT_GENERATION_KEY,
            str(next_generation),
        )
        _set_preference(
            session,
            ACCOUNT_ENROLLMENT_RESET_COMMIT_KEY,
            deployment_id,
        )

        # Old account-specific strategy epochs must never be inherited by new rows.
        session.execute(
            delete(RuntimePreference).where(
                RuntimePreference.preference_key.like(
                    "hybrid_over2_put_v4:account_epoch:%"
                )
            )
        )
        session.execute(
            delete(RuntimePreference).where(
                RuntimePreference.preference_key.like(
                    "hybrid_o2u7_put_v1:account_epoch:%"
                )
            )
        )

        session.add(
            AuditEvent(
                action="ACCOUNT_ENROLLMENT_GENERATION_RESET",
                actor="vps-deployment",
                source_ip="local",
                details={
                    "deployment_id": deployment_id,
                    "previous_generation": current_generation,
                    "new_generation": next_generation,
                    "archived_accounts": total_accounts,
                    "stopped_enabled_accounts": enabled_accounts,
                    "invalidated_sessions": int(invalidated_sessions),
                    "discarded_oauth_states": int(discarded_oauth_states),
                    "cleared_trader_leases": int(cleared_leases),
                    "historical_trades_preserved": True,
                },
            )
        )

        result = {
            "status": "reset_complete",
            "deployment_id": deployment_id,
            "previous_generation": current_generation,
            "new_generation": next_generation,
            "archived_accounts": total_accounts,
            "stopped_enabled_accounts": enabled_accounts,
            "invalidated_browser_sessions": int(invalidated_sessions),
            "discarded_oauth_states": int(discarded_oauth_states),
            "cleared_trader_leases": int(cleared_leases),
            "current_generation_accounts": 0,
            "historical_trades_preserved": True,
            "next_action": (
                "Log in through Deriv, save the trading API token when required, "
                "then start auto trading for the owner account only."
            ),
        }

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
