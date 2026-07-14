"""Store authoritative Deriv contract economics and markup."""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0004"
down_revision = "20260709_0003"
branch_labels = None
depends_on = None


ECONOMIC_COLUMNS = (
    "buy_price",
    "payout",
    "app_markup_amount",
    "commission",
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("trades")}
    with op.batch_alter_table("trades") as batch:
        for name in ECONOMIC_COLUMNS:
            if name not in existing:
                batch.add_column(sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("trades")}
    with op.batch_alter_table("trades") as batch:
        for name in reversed(ECONOMIC_COLUMNS):
            if name in existing:
                batch.drop_column(name)
