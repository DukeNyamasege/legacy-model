from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.models import ManagedAccount, utc_now
from app.premium_access_models import PremiumCustomer, PremiumCustomerAccount
from app.premium_renewal_models import PremiumAccessPeriod


WEEKLY_PERIOD_DAYS = 7
WEEKLY_PRICE_KES = 250.0
WEEKLY_AMOUNT_MINOR_KES = 25000
PLAN_CODE = "weekly_access"
PREMIUM_REQUIRED_REASON = (
    "Premium access expired. Renew KES 250 via M-Pesa for another 7 days."
)


@dataclass(frozen=True)
class PremiumAccessState:
    customer_id: str | None
    active: bool
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    remaining_seconds: int
    renewal_preference: str
    auto_renew_enabled: bool
    renewal_provider: str
    provider_subscription_ref: str


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_account_id(value: Any) -> str:
    return str(value or "").strip().upper()


def premium_account_hash(account_id: Any) -> str:
    normalized = normalize_account_id(account_id)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def premium_identity_fingerprint(account_ids: list[str]) -> str:
    hashes = sorted(
        {premium_account_hash(account_id) for account_id in account_ids if account_id}
    )
    if not hashes:
        raise ValueError("At least one linked Deriv Options account is required")
    return hashlib.sha256("|".join(hashes).encode("utf-8")).hexdigest()


def effective_access_state(
    customer: PremiumCustomer | None,
    *,
    now: datetime | None = None,
) -> PremiumAccessState:
    current = _as_utc(now) or utc_now()
    if customer is None:
        return PremiumAccessState(
            customer_id=None,
            active=False,
            status="unpaid",
            current_period_start=None,
            current_period_end=None,
            remaining_seconds=0,
            renewal_preference="prompt_again",
            auto_renew_enabled=False,
            renewal_provider="lipana",
            provider_subscription_ref="",
        )

    starts_at = _as_utc(customer.current_period_start)
    ends_at = _as_utc(customer.current_period_end)
    stored_status = str(customer.status or "unpaid").strip().lower() or "unpaid"
    active = bool(stored_status == "active" and ends_at is not None and current < ends_at)
    effective_status = stored_status
    if ends_at is not None and current >= ends_at and stored_status == "active":
        effective_status = "expired"
    remaining = (
        max(0, int((ends_at - current).total_seconds()))
        if active and ends_at is not None
        else 0
    )
    return PremiumAccessState(
        customer_id=str(customer.id),
        active=active,
        status=effective_status,
        current_period_start=starts_at,
        current_period_end=ends_at,
        remaining_seconds=remaining,
        renewal_preference=str(customer.renewal_preference or "prompt_again"),
        auto_renew_enabled=bool(customer.auto_renew_enabled),
        renewal_provider=str(customer.renewal_provider or "lipana"),
        provider_subscription_ref=str(customer.provider_subscription_ref or ""),
    )


def renewal_reminder_payload(state: PremiumAccessState) -> dict[str, Any]:
    """Return the exact UI/API renewal stage without changing entitlement state."""

    seconds = max(0, int(state.remaining_seconds))
    if state.active:
        if seconds <= 3600:
            stage = "one_hour"
            message = "Less than 1 hour of Premium remains. Renew after expiry to continue."
        elif seconds <= 6 * 3600:
            stage = "six_hours"
            message = "Less than 6 hours of Premium remains."
        elif seconds <= 24 * 3600:
            stage = "twenty_four_hours"
            message = "Less than 24 hours of Premium remains."
        else:
            stage = "active"
            message = "Premium access is active."
        return {
            "stage": stage,
            "message": message,
            "renewal_required": False,
            "renewal_available": False,
            "remaining_seconds": seconds,
        }

    expired = state.status == "expired"
    return {
        "stage": "expired" if expired else "payment_required",
        "message": (
            "Premium has expired. Pay KES 250 via M-Pesa to start a new 7-day period."
            if expired
            else "Pay KES 250 via M-Pesa to activate Premium for 7 days."
        ),
        "renewal_required": expired,
        "renewal_available": True,
        "remaining_seconds": 0,
    }


def _customer_for_mapping(
    session: Any,
    mapping: PremiumCustomerAccount | None,
) -> PremiumCustomer | None:
    if mapping is None:
        return None
    return session.get(PremiumCustomer, str(mapping.customer_id))


def premium_access_for_managed_account(
    database: Any,
    managed_account_id: int,
    *,
    now: datetime | None = None,
) -> PremiumAccessState:
    """O(1) entitlement lookup used by the financial worker boundary."""

    with database.session() as session:
        mapping = session.scalar(
            select(PremiumCustomerAccount).where(
                PremiumCustomerAccount.managed_account_id == int(managed_account_id)
            )
        )
        return effective_access_state(
            _customer_for_mapping(session, mapping),
            now=now,
        )


def premium_access_for_account_hash(
    database: Any,
    account_hash: str,
    *,
    now: datetime | None = None,
) -> PremiumAccessState:
    with database.session() as session:
        mapping = session.scalar(
            select(PremiumCustomerAccount).where(
                PremiumCustomerAccount.account_hash == str(account_hash)
            )
        )
        return effective_access_state(
            _customer_for_mapping(session, mapping),
            now=now,
        )


def expire_customer_if_needed(
    database: Any,
    customer_id: str,
    *,
    now: datetime | None = None,
) -> PremiumAccessState:
    """Persist EXPIRED lazily while keeping the timestamp itself authoritative."""

    current = _as_utc(now) or utc_now()
    with database.session() as session:
        customer = session.get(PremiumCustomer, str(customer_id), with_for_update=True)
        if customer is None:
            return effective_access_state(None, now=current)
        state = effective_access_state(customer, now=current)
        if state.status == "expired" and str(customer.status).lower() == "active":
            customer.status = "expired"
            customer.updated_at = current
        return state


def _ensure_access_period(
    session: Any,
    *,
    customer_id: str,
    provider: str,
    payment_reference: str,
    starts_at: datetime,
    ends_at: datetime,
) -> PremiumAccessPeriod:
    existing = session.scalar(
        select(PremiumAccessPeriod).where(
            PremiumAccessPeriod.provider == str(provider),
            PremiumAccessPeriod.payment_reference == str(payment_reference),
        )
    )
    if existing is not None:
        return existing
    row = PremiumAccessPeriod(
        id=str(uuid4()),
        customer_id=str(customer_id),
        provider=str(provider)[:32],
        payment_method="mpesa",
        payment_reference=str(payment_reference)[:160],
        amount_minor=WEEKLY_AMOUNT_MINOR_KES,
        currency="KES",
        period_start=starts_at,
        period_end=ends_at,
        created_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def activate_weekly_access(
    database: Any,
    customer_id: str,
    *,
    paid_at: datetime,
    provider: str,
    payment_reference: str,
    auto_renew_enabled: bool,
    provider_customer_ref: str = "",
    provider_subscription_ref: str = "",
    renewal_preference: str | None = None,
) -> PremiumAccessState:
    """Activate one exact seven-day period after a server-verified payment.

    The provider/payment reference pair is idempotent. A webhook replay can never
    grant a second week or move the expiry timestamp. Lipana renewal is manual:
    the next STK request is accepted only after the prior exact period has ended.
    """

    starts_at = _as_utc(paid_at)
    if starts_at is None:
        raise ValueError("A verified payment timestamp is required")
    normalized_provider = str(provider or "")[:32]
    normalized_reference = str(payment_reference or "")[:160]
    if not normalized_provider or not normalized_reference:
        raise ValueError("Verified provider and payment reference are required")
    ends_at = starts_at + timedelta(days=WEEKLY_PERIOD_DAYS)
    now = utc_now()
    with database.session() as session:
        customer = session.get(PremiumCustomer, str(customer_id), with_for_update=True)
        if customer is None:
            raise ValueError("Premium customer was not found")

        if (
            str(customer.last_payment_provider or "") == normalized_provider
            and str(customer.last_payment_reference or "") == normalized_reference
            and customer.current_period_start is not None
            and customer.current_period_end is not None
        ):
            _ensure_access_period(
                session,
                customer_id=str(customer.id),
                provider=normalized_provider,
                payment_reference=normalized_reference,
                starts_at=_as_utc(customer.current_period_start) or starts_at,
                ends_at=_as_utc(customer.current_period_end) or ends_at,
            )
            return effective_access_state(customer, now=now)

        customer.status = "active"
        customer.plan_code = PLAN_CODE
        customer.current_period_start = starts_at
        customer.current_period_end = ends_at
        customer.auto_renew_enabled = bool(auto_renew_enabled)
        customer.renewal_provider = normalized_provider
        if renewal_preference is not None:
            customer.renewal_preference = str(renewal_preference or "")[:32]
        customer.provider_customer_ref = str(provider_customer_ref or "")[:160]
        customer.provider_subscription_ref = str(provider_subscription_ref or "")[:160]
        customer.last_payment_provider = normalized_provider
        customer.last_payment_reference = normalized_reference
        customer.renewal_failed_at = None
        customer.cancellation_requested_at = None
        customer.updated_at = now
        _ensure_access_period(
            session,
            customer_id=str(customer.id),
            provider=normalized_provider,
            payment_reference=normalized_reference,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        return effective_access_state(customer, now=starts_at)


def record_renewal_failure(
    database: Any,
    customer_id: str,
    *,
    failed_at: datetime,
) -> PremiumAccessState:
    """A failed renewal never extends access beyond the already-paid period."""

    failure_time = _as_utc(failed_at) or utc_now()
    with database.session() as session:
        customer = session.get(PremiumCustomer, str(customer_id), with_for_update=True)
        if customer is None:
            raise ValueError("Premium customer was not found")
        customer.renewal_failed_at = failure_time
        customer.updated_at = failure_time
        state = effective_access_state(customer, now=failure_time)
        if not state.active:
            customer.status = "expired" if customer.current_period_end else "unpaid"
        return effective_access_state(customer, now=failure_time)


def premium_access_period_history(
    database: Any,
    customer_id: str,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = _as_utc(now) or utc_now()
    safe_limit = max(1, min(200, int(limit)))
    with database.session() as session:
        rows = list(
            session.scalars(
                select(PremiumAccessPeriod)
                .where(PremiumAccessPeriod.customer_id == str(customer_id))
                .order_by(PremiumAccessPeriod.period_start.desc())
                .limit(safe_limit)
            ).all()
        )
    result: list[dict[str, Any]] = []
    for row in rows:
        starts_at = _as_utc(row.period_start) or current
        ends_at = _as_utc(row.period_end) or starts_at
        if current < starts_at:
            status = "upcoming"
        elif current < ends_at:
            status = "active"
        else:
            status = "expired"
        result.append(
            {
                "id": str(row.id),
                "provider": str(row.provider),
                "payment_method": str(row.payment_method),
                "amount": float(row.amount_minor) / 100.0,
                "currency": str(row.currency),
                "period_start": starts_at.isoformat(),
                "period_end": ends_at.isoformat(),
                "status": status,
            }
        )
    return result


def pause_managed_account_for_premium(
    database: Any,
    managed_account_id: int,
    *,
    reason: str = PREMIUM_REQUIRED_REASON,
) -> bool:
    """Stop new execution while leaving already-open contracts available to settle."""

    with database.session() as session:
        row = session.get(ManagedAccount, int(managed_account_id), with_for_update=True)
        if row is None:
            return False
        changed = bool(row.enabled) or str(row.execution_status or "") != "manual_pause"
        row.enabled = False
        row.execution_status = "manual_pause"
        row.execution_status_reason = str(reason or PREMIUM_REQUIRED_REASON)[:160]
        row.execution_status_updated_at = utc_now()
        row.updated_at = utc_now()
        return changed


def access_payload(
    state: PremiumAccessState,
    *,
    linked_account_count: int = 0,
    checkout_ready: bool = False,
) -> dict[str, Any]:
    return {
        "active": bool(state.active),
        "status": state.status,
        "premium_required": not bool(state.active),
        "plan": {
            "code": PLAN_CODE,
            "name": "DerivAdmin Premium Weekly",
            "duration_days": WEEKLY_PERIOD_DAYS,
        },
        "pricing": {
            "mpesa": {"amount": WEEKLY_PRICE_KES, "currency": "KES"},
        },
        "current_period_start": (
            state.current_period_start.isoformat()
            if state.current_period_start is not None
            else None
        ),
        "expires_at": (
            state.current_period_end.isoformat()
            if state.current_period_end is not None
            else None
        ),
        "remaining_seconds": int(state.remaining_seconds),
        "linked_account_count": int(linked_account_count),
        "renewal": {
            "mode": "manual_mpesa_after_exact_expiry",
            "preference": state.renewal_preference,
            "auto_renew_enabled": False,
            "provider": "lipana",
            "payment_method": "mpesa",
            "failure_policy": "expire_and_require_successful_mpesa_payment",
            **renewal_reminder_payload(state),
        },
        "checkout_ready": bool(checkout_ready),
    }
