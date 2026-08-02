#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api import DATABASE  # noqa: E402


CONFIRM = "MOVE_RUNAWAY_RECOVERY_TO_VIRTUAL"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move runaway AIDR recovery states back to virtual protection."
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != CONFIRM:
        raise SystemExit(f"Refusing to run. Use --confirm {CONFIRM}")

    with DATABASE.session() as session:
        before = session.execute(
            text(
                """
                SELECT
                  ars.managed_account_id,
                  ma.label,
                  ma.enabled,
                  ma.execution_status,
                  ma.stake_amount,
                  ars.consecutive_losses,
                  ars.recovery_loss_debt,
                  ars.recovery_pending,
                  ars.recovery_attempt_active,
                  ars.protection_mode,
                  ars.virtual_win_count,
                  ars.virtual_loss_count
                FROM account_risk_states ars
                JOIN managed_accounts ma ON ma.id = ars.managed_account_id
                WHERE ars.recovery_loss_debt > 0.009
                  AND ars.protection_mode <> 'VIRTUAL_WAITING_FOR_WIN'
                  AND (
                    ars.consecutive_losses >= 2
                    OR ars.recovery_loss_debt > GREATEST(COALESCE(ma.stake_amount, 0.5) * 2.10, COALESCE(ma.stake_amount, 0.5) + 0.05)
                    OR lower(coalesce(ma.execution_status, '')) = 'recovery_pending'
                  )
                ORDER BY ars.recovery_loss_debt DESC
                """
            )
        ).mappings().all()

        ids = [int(row["managed_account_id"]) for row in before]
        if ids:
            session.execute(
                text(
                    """
                    UPDATE account_risk_states
                    SET protection_mode = 'VIRTUAL_WAITING_FOR_WIN',
                        recovery_pending = true,
                        recovery_attempt_active = false,
                        entered_virtual_mode_at = COALESCE(entered_virtual_mode_at, NOW()),
                        recovery_pending_since = COALESCE(recovery_pending_since, NOW()),
                        virtual_observation_count = 0,
                        virtual_win_count = 0,
                        virtual_loss_count = 0,
                        current_virtual_loss_streak = 0,
                        updated_at = NOW()
                    WHERE managed_account_id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            )
            session.execute(
                text(
                    """
                    UPDATE managed_accounts
                    SET execution_status = 'virtual_protection',
                        execution_status_reason = 'Strict AIDR repair: failed recovery moved to virtual mode. Real contracts blocked until 2 consecutive virtual OVER-3 wins.',
                        updated_at = NOW()
                    WHERE id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            )
            # Remove split-recovery runtime flags for repaired accounts so the
            # next real recovery is allowed only after virtual confirmation.
            session.execute(
                text(
                    """
                    DELETE FROM runtime_preferences
                    WHERE key = ANY(:keys)
                    """
                ),
                {"keys": [f"aidr_split_remaining:{account_id}" for account_id in ids]},
            )

        after = session.execute(
            text(
                """
                SELECT
                  ars.managed_account_id,
                  ma.label,
                  ma.execution_status,
                  ars.recovery_loss_debt,
                  ars.protection_mode,
                  ars.virtual_win_count,
                  ars.virtual_loss_count
                FROM account_risk_states ars
                JOIN managed_accounts ma ON ma.id = ars.managed_account_id
                WHERE ars.managed_account_id = ANY(:ids)
                ORDER BY ars.recovery_loss_debt DESC
                """
            ),
            {"ids": ids or [-1]},
        ).mappings().all()

    print("AIDR_RUNAWAY_RECOVERY_REPAIR_COMPLETE")
    print(f"repaired_accounts={len(ids)}")
    for row in before[:50]:
        print(
            "before",
            f"id={row['managed_account_id']}",
            f"label={row['label']}",
            f"enabled={row['enabled']}",
            f"status={row['execution_status']}",
            f"stake={row['stake_amount']}",
            f"losses={row['consecutive_losses']}",
            f"debt={row['recovery_loss_debt']}",
            f"mode={row['protection_mode']}",
        )
    for row in after[:50]:
        print(
            "after",
            f"id={row['managed_account_id']}",
            f"label={row['label']}",
            f"status={row['execution_status']}",
            f"debt={row['recovery_loss_debt']}",
            f"mode={row['protection_mode']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
