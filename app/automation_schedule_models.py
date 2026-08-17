from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utc_now


class AutomationSchedule(Base):
    """One persistent, one-shot automated trading session.

    The row is both the future schedule and its immutable lifecycle history. The
    scheduler never relies on a browser timer/localStorage after creation.
    """

    __tablename__ = "automation_schedules"
    __table_args__ = (
        Index("ix_automation_schedule_due", "status", "scheduled_for_utc"),
        Index("ix_automation_schedule_account", "managed_account_id", "scheduled_for_utc"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    managed_account_id: Mapped[int] = mapped_column(
        ForeignKey("managed_accounts.id"), nullable=False, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False)
    strategy_source: Mapped[str] = mapped_column(String(40), default="saved")
    strategy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    scheduled_local: Mapped[str] = mapped_column(String(32), nullable=False)
    scheduled_for_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    stake_amount: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss: Mapped[float] = mapped_column(Float, default=0.0)
    overlap_policy: Mapped[str] = mapped_column(String(16), default="wait")

    status: Mapped[str] = mapped_column(String(20), default="scheduled", index=True)
    status_reason: Mapped[str] = mapped_column(Text, default="")
    claimed_by: Mapped[str] = mapped_column(String(120), default="")
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
