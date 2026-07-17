"""Add persistent one-shot recovery state to each managed account."""

from alembic import op
import sqlalchemy as sa


revision = "20260717_0008"
down_revision = "20260717_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("account_risk_states")
    }
    additions = (
        (
            "recovery_loss_debt",
            sa.Column(
                "recovery_loss_debt",
                sa.Float(),
                server_default="0",
                nullable=False,
            ),
        ),
        (
            "recovery_pending",
            sa.Column(
                "recovery_pending",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        ),
        (
            "recovery_attempt_active",
            sa.Column(
                "recovery_attempt_active",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        ),
    )
    for name, column in additions:
        if name not in existing:
            op.add_column("account_risk_states", column)


def downgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("account_risk_states")
    }
    for name in (
        "recovery_attempt_active",
        "recovery_pending",
        "recovery_loss_debt",
    ):
        if name in existing:
            op.drop_column("account_risk_states", name)
