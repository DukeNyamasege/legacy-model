"""Add personal execution controls and per-account worker health."""

from alembic import op
import sqlalchemy as sa


revision = "20260716_0006"
down_revision = "20260715_0005"
branch_labels = None
depends_on = None


ACCOUNT_COLUMNS = (
    ("stake_amount", sa.Float(), False, sa.text("0.50")),
    ("take_profit", sa.Float(), False, sa.text("0")),
    ("stop_loss", sa.Float(), False, sa.text("0")),
    ("execution_status", sa.String(length=30), False, sa.text("'inactive'")),
    ("execution_status_reason", sa.String(length=160), False, sa.text("''")),
    (
        "execution_status_updated_at",
        sa.DateTime(timezone=True),
        False,
        sa.text("CURRENT_TIMESTAMP"),
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"] for column in sa.inspect(bind).get_columns("managed_accounts")
    }
    with op.batch_alter_table("managed_accounts") as batch:
        for name, column_type, nullable, server_default in ACCOUNT_COLUMNS:
            if name not in existing:
                batch.add_column(
                    sa.Column(
                        name,
                        column_type,
                        nullable=nullable,
                        server_default=server_default,
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"] for column in sa.inspect(bind).get_columns("managed_accounts")
    }
    with op.batch_alter_table("managed_accounts") as batch:
        for name, _column_type, _nullable, _server_default in reversed(ACCOUNT_COLUMNS):
            if name in existing:
                batch.drop_column(name)
