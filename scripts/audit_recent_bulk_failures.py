from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.config import load_test2_config
from app.database import Database
from app.models import BulkExecutionBatch, BulkExecutionMember
from enhanced_bot import sanitize_account_ids


def _safe(value: object, limit: int = 220) -> str:
    return sanitize_account_ids(" ".join(str(value or "-").split()))[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only masked audit of recent REST bulk-purchase member failures. "
            "Credentials are never loaded or printed."
        )
    )
    parser.add_argument("--minutes", type=float, default=30.0)
    args = parser.parse_args()

    minutes = max(1.0, float(args.minutes))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    config_path = os.getenv("DERIV_BOT_CONFIG", str(ROOT / "config.yaml"))
    config = load_test2_config(config_path)
    database = Database(config.database_url)

    print("=== RECENT BULK MEMBER FAILURES ===")
    print(f"since={cutoff.isoformat()} window_minutes={minutes:.1f}")
    print("credentials_loaded=false raw_tokens_printed=false")

    with database.session() as session:
        rows = session.execute(
            select(BulkExecutionBatch, BulkExecutionMember)
            .join(
                BulkExecutionMember,
                BulkExecutionMember.batch_id == BulkExecutionBatch.id,
            )
            .where(
                BulkExecutionBatch.request_started_at >= cutoff,
                BulkExecutionMember.status != "SUCCESS",
            )
            .order_by(
                BulkExecutionBatch.request_started_at.desc(),
                BulkExecutionMember.id.desc(),
            )
            .limit(50)
        ).all()

        if not rows:
            print("none")
            return 0

        for batch, member in rows:
            metadata = dict(batch.request_metadata or {})
            print(
                " ".join(
                    (
                        f"time={batch.request_started_at.isoformat()}",
                        f"batch={batch.id}",
                        f"batch_status={batch.status}",
                        f"account={_safe(member.account_id_masked, 50)}",
                        f"member_status={_safe(member.status, 30)}",
                        f"strategy={_safe(metadata.get('strategy_group'), 80)}",
                        f"contract={_safe(metadata.get('contract_type'), 30)}",
                        f"barrier={_safe(metadata.get('barrier'), 10)}",
                        f"stake={float(batch.stake or 0.0):.2f}",
                        f"error_code={_safe(member.error_code, 80)}",
                        f"error={_safe(member.error_message)}",
                    )
                )
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
