"""Store provider contract timing separately from local lifecycle timing."""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0005"
down_revision = "20260715_0004"
branch_labels = None
depends_on = None


TIMING_COLUMNS = (
    ("provider_purchase_time", sa.DateTime(timezone=True), True, None),
    ("provider_start_time", sa.DateTime(timezone=True), True, None),
    ("provider_expiry_time", sa.DateTime(timezone=True), True, None),
    ("provider_settlement_time", sa.DateTime(timezone=True), True, None),
    ("contract_duration", sa.Integer(), False, sa.text("1")),
    ("contract_duration_unit", sa.String(length=10), False, sa.text("'t'")),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("trades")}
    with op.batch_alter_table("trades") as batch:
        for name, column_type, nullable, server_default in TIMING_COLUMNS:
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
    existing = {column["name"] for column in sa.inspect(bind).get_columns("trades")}
    with op.batch_alter_table("trades") as batch:
        for name, _column_type, _nullable, _server_default in reversed(TIMING_COLUMNS):
            if name in existing:
                batch.drop_column(name)
