"""Store OAuth PKCE login state server-side."""

from alembic import op
import sqlalchemy as sa


revision = "20260709_0003"
down_revision = "20260709_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = sa.Table(
        "oauth_login_states",
        metadata,
        sa.Column("state_hash", sa.String(length=64), primary_key=True),
        sa.Column("code_verifier_secret", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    table.create(bind, checkfirst=True)
    sa.Index(
        "ix_oauth_login_states_expires_at",
        table.c.expires_at,
    ).create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = sa.Table("oauth_login_states", metadata)
    table.drop(bind, checkfirst=True)
