"""Remove the automatic consecutive-loss account stop."""

from alembic import op
import sqlalchemy as sa


revision = "20260718_0010"
down_revision = "20260718_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    managed_accounts = sa.table(
        "managed_accounts",
        sa.column("enabled", sa.Boolean()),
        sa.column("execution_status", sa.String()),
        sa.column("execution_status_reason", sa.String()),
        sa.column("execution_status_updated_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        managed_accounts.update()
        .where(managed_accounts.c.execution_status == "session_loss_stop")
        .values(
            enabled=True,
            execution_status="active",
            execution_status_reason=(
                "Consecutive-loss stop removed; execution continues until TP/SL or user stop"
            ),
            execution_status_updated_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


def downgrade() -> None:
    # Reintroducing the rule must not unexpectedly disable live accounts.
    pass
