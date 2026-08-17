"""persistent automation schedules for Action 5

Revision ID: 20260817_0022
Revises: 20260812_0021
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260817_0022"
down_revision = "20260812_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_schedules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "managed_account_id",
            sa.Integer(),
            sa.ForeignKey("managed_accounts.id"),
            nullable=False,
        ),
        sa.Column("strategy_name", sa.String(length=120), nullable=False),
        sa.Column("strategy_source", sa.String(length=40), nullable=False, server_default="saved"),
        sa.Column("strategy_snapshot", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("scheduled_local", sa.String(length=32), nullable=False),
        sa.Column("scheduled_for_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stake_amount", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False, server_default="0"),
        sa.Column("stop_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("overlap_policy", sa.String(length=16), nullable=False, server_default="wait"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="scheduled"),
        sa.Column("status_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("claimed_by", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_automation_schedules_managed_account_id",
        "automation_schedules",
        ["managed_account_id"],
    )
    op.create_index(
        "ix_automation_schedules_scheduled_for_utc",
        "automation_schedules",
        ["scheduled_for_utc"],
    )
    op.create_index(
        "ix_automation_schedules_status",
        "automation_schedules",
        ["status"],
    )
    op.create_index(
        "ix_automation_schedule_due",
        "automation_schedules",
        ["status", "scheduled_for_utc"],
    )
    op.create_index(
        "ix_automation_schedule_account",
        "automation_schedules",
        ["managed_account_id", "scheduled_for_utc"],
    )


def downgrade() -> None:
    op.drop_index("ix_automation_schedule_account", table_name="automation_schedules")
    op.drop_index("ix_automation_schedule_due", table_name="automation_schedules")
    op.drop_index("ix_automation_schedules_status", table_name="automation_schedules")
    op.drop_index("ix_automation_schedules_scheduled_for_utc", table_name="automation_schedules")
    op.drop_index("ix_automation_schedules_managed_account_id", table_name="automation_schedules")
    op.drop_table("automation_schedules")
