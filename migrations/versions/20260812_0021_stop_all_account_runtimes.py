"""stop all account runtimes for builder-first migration

Revision ID: 20260812_0021
Revises: 20260805_0020
Create Date: 2026-08-12
"""

from __future__ import annotations

from alembic import op


revision = "20260812_0021"
down_revision = "20260805_0020"
branch_labels = None
depends_on = None


STOP_REASON = (
    "Builder-first migration: Auto Trading is OFF. Press Start Auto Trading to execute."
)


def upgrade() -> None:
    # Preserve every account and credential, but fail closed. The new runtime
    # starts only after the specific user explicitly presses Start Auto Trading.
    op.execute(
        f"""
        UPDATE managed_accounts
        SET enabled = FALSE,
            execution_status = 'stopped',
            execution_status_reason = '{STOP_REASON}',
            execution_status_updated_at = NOW(),
            updated_at = NOW()
        """
    )
    op.execute(
        """
        UPDATE bot_state
        SET status = 'STOPPED',
            pause_reason = 'BUILDER_FIRST_MIGRATION_STOPPED',
            last_heartbeat = NOW()
        """
    )
    op.execute(
        """
        DELETE FROM trader_leases
        """
    )


def downgrade() -> None:
    # Intentionally no-op. Re-enabling trading must remain an explicit user action.
    return
