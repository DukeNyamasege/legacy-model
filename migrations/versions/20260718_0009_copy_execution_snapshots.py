"""Record the expected and registered accounts for each purchase signal."""

from alembic import op
import sqlalchemy as sa


revision = "20260718_0009"
down_revision = "20260717_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("candidate_signals")
    }
    for name in ("expected_account_masks", "registered_account_masks"):
        if name not in existing:
            op.add_column(
                "candidate_signals",
                sa.Column(
                    name,
                    sa.JSON(),
                    server_default=sa.text("'[]'"),
                    nullable=False,
                ),
            )


def downgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("candidate_signals")
    }
    for name in ("registered_account_masks", "expected_account_masks"):
        if name in existing:
            op.drop_column("candidate_signals", name)
