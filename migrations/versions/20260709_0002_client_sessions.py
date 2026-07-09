"""Add browser client sessions for Deriv OAuth dashboard auth."""

from alembic import op
import sqlalchemy as sa


revision = "20260709_0002"
down_revision = "20260702_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = sa.Table(
        "client_sessions",
        metadata,
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
    table.create(bind, checkfirst=True)
    sa.Index(
        "ix_client_sessions_managed_account_id",
        table.c.managed_account_id,
    ).create(bind, checkfirst=True)
    sa.Index("ix_client_sessions_expires_at", table.c.expires_at).create(
        bind,
        checkfirst=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = sa.Table("client_sessions", metadata)
    table.drop(bind, checkfirst=True)
