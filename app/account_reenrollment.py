from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Table, func, select

from app.models import Base, ManagedAccount, RuntimePreference, utc_now
from app.repositories.test2_repository import Test2Repository


ACCOUNT_ENROLLMENT_GENERATION_KEY = "account_enrollment_generation"
ACCOUNT_ENROLLMENT_RESET_COMMIT_KEY = "account_enrollment_reset_commit"

# Keep historical ManagedAccount rows and all of their trading relationships intact.
# A reset only advances the current generation. New OAuth links receive a row in this
# table, while previous-generation accounts become invisible to the API and worker.
ACCOUNT_ENROLLMENTS = Table(
    "account_enrollments",
    Base.metadata,
    Column(
        "managed_account_id",
        Integer,
        ForeignKey("managed_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("generation", Integer, nullable=False, index=True),
    Column("enrolled_at", DateTime(timezone=True), nullable=False, default=utc_now),
    extend_existing=True,
)

_INSTALLED = False


def current_account_generation(repository: Test2Repository) -> int:
    with repository.database.session() as session:
        row = session.get(RuntimePreference, ACCOUNT_ENROLLMENT_GENERATION_KEY)
        try:
            return max(0, int(row.preference_value if row else 0))
        except (TypeError, ValueError):
            return 0


def install_account_reenrollment() -> None:
    """Make repository account reads generation-aware without deleting history.

    Generation 0 preserves the legacy behaviour exactly. Once the administrator
    runs the reset script, only accounts linked in the new generation are returned
    to the API or worker. OAuth therefore creates fresh disabled registrations and
    each trader must explicitly start auto trading again.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_list_managed_accounts = Test2Repository.list_managed_accounts
    original_managed_account_count = Test2Repository.managed_account_count
    original_add_managed_account = Test2Repository.add_managed_account

    def list_current_generation_accounts(
        self: Test2Repository,
    ) -> list[ManagedAccount]:
        generation = current_account_generation(self)
        if generation <= 0:
            return original_list_managed_accounts(self)
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(ManagedAccount)
                    .join(
                        ACCOUNT_ENROLLMENTS,
                        ACCOUNT_ENROLLMENTS.c.managed_account_id == ManagedAccount.id,
                    )
                    .where(ACCOUNT_ENROLLMENTS.c.generation == generation)
                    .order_by(ManagedAccount.created_at, ManagedAccount.id)
                ).all()
            )

    def count_current_generation_accounts(self: Test2Repository) -> int:
        generation = current_account_generation(self)
        if generation <= 0:
            return original_managed_account_count(self)
        with self.database.session() as session:
            return int(
                session.scalar(
                    select(func.count(ManagedAccount.id))
                    .join(
                        ACCOUNT_ENROLLMENTS,
                        ACCOUNT_ENROLLMENTS.c.managed_account_id == ManagedAccount.id,
                    )
                    .where(ACCOUNT_ENROLLMENTS.c.generation == generation)
                )
                or 0
            )

    def add_current_generation_account(
        self: Test2Repository,
        *,
        label: str,
        token_secret: str,
        enabled: bool = True,
    ) -> dict:
        result = original_add_managed_account(
            self,
            label=label,
            token_secret=token_secret,
            enabled=enabled,
        )
        generation = current_account_generation(self)
        if generation > 0:
            with self.database.session() as session:
                session.execute(
                    ACCOUNT_ENROLLMENTS.insert().values(
                        managed_account_id=int(result["id"]),
                        generation=generation,
                        enrolled_at=utc_now(),
                    )
                )
        return result

    Test2Repository.list_managed_accounts = list_current_generation_accounts
    Test2Repository.managed_account_count = count_current_generation_accounts
    Test2Repository.add_managed_account = add_current_generation_account
    Test2Repository._account_reenrollment_installed = True
    _INSTALLED = True
