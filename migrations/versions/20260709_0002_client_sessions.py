"""Add browser client sessions for Deriv OAuth dashboard auth."""

from alembic import op
import sqlalchemy as sa


revision = "20260709_0002"
down_revision = "20260702_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_sessions",
        sa.Column("session_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "managed_account_id",
            sa.Integer(),
            sa.ForeignKey("managed_accounts.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_client_sessions_managed_account_id",
        "client_sessions",
        ["managed_account_id"],
    )
    op.create_index("ix_client_sessions_expires_at", "client_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_client_sessions_expires_at", table_name="client_sessions")
    op.drop_index("ix_client_sessions_managed_account_id", table_name="client_sessions")
    op.drop_table("client_sessions")
