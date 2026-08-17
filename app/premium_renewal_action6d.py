from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException, Request
from sqlalchemy import select

import app.api as base_api
from app import automation_scheduler_action5 as scheduler
from app.automation_schedule_models import AutomationSchedule
from app.models import ManagedAccount, utc_now
from app.premium_access_api import ensure_premium_customer
from app.premium_access_models import PremiumCustomer, PremiumCustomerAccount
from app.premium_access_service import (
    PREMIUM_REQUIRED_REASON,
    access_payload,
    effective_access_state,
    premium_access_period_history,
    renewal_reminder_payload,
)


LOGGER = logging.getLogger("legacy_model.premium_renewal_action6d")
_INSTALLED = False
_ORIGINAL_SCHEDULE_APPLY: Callable[[str], tuple[bool, str]] | None = None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def run_premium_expiry_cycle(*, now: datetime | None = None) -> dict[str, int]:
    """Persist due expiry and pause fresh execution without touching settlements.

    Exact enforcement does not depend on this sweep: API mutations and the worker
    compare the request/purchase time directly with current_period_end. This cycle
    exists to persist EXPIRED promptly and remove expired accounts from active
    runtime admission even when the trader has no browser open.
    """

    current = _as_utc(now) or utc_now()
    expired_customers = 0
    paused_accounts = 0

    with base_api.DATABASE.session() as session:
        candidates = list(
            session.scalars(
                select(PremiumCustomer).where(
                    PremiumCustomer.status == "active",
                    PremiumCustomer.current_period_end.is_not(None),
                    PremiumCustomer.current_period_end <= current,
                )
            ).all()
        )
        for candidate in candidates:
            customer = session.get(
                PremiumCustomer,
                str(candidate.id),
                with_for_update=True,
            )
            if customer is None:
                continue
            state = effective_access_state(customer, now=current)
            if state.active or state.status != "expired":
                continue

            customer.status = "expired"
            customer.updated_at = current
            expired_customers += 1

            mappings = list(
                session.scalars(
                    select(PremiumCustomerAccount).where(
                        PremiumCustomerAccount.customer_id == str(customer.id),
                        PremiumCustomerAccount.managed_account_id.is_not(None),
                    )
                ).all()
            )
            for mapping in mappings:
                managed_id = int(mapping.managed_account_id or 0)
                if managed_id <= 0:
                    continue
                account = session.get(
                    ManagedAccount,
                    managed_id,
                    with_for_update=True,
                )
                if account is None or not bool(account.enabled):
                    continue
                account.enabled = False
                account.execution_status = "manual_pause"
                account.execution_status_reason = PREMIUM_REQUIRED_REASON[:160]
                account.execution_status_updated_at = current
                account.updated_at = current
                paused_accounts += 1

    if expired_customers:
        LOGGER.info(
            "PREMIUM_EXACT_EXPIRY_SWEEP expired_customers=%s paused_accounts=%s",
            expired_customers,
            paused_accounts,
        )
    return {
        "expired_customers": expired_customers,
        "paused_accounts": paused_accounts,
    }


def _premium_state_for_schedule(session: Any, managed_account_id: int, now: datetime) -> Any:
    mapping = session.scalar(
        select(PremiumCustomerAccount).where(
            PremiumCustomerAccount.managed_account_id == int(managed_account_id)
        )
    )
    customer = (
        session.get(PremiumCustomer, str(mapping.customer_id))
        if mapping is not None
        else None
    )
    return effective_access_state(customer, now=now)


def _premium_schedule_apply(schedule_id: str) -> tuple[bool, str]:
    """Prevent a previously-created schedule from starting after entitlement expiry."""

    current = utc_now()
    blocked = False
    with base_api.DATABASE.session() as session:
        row = session.get(
            AutomationSchedule,
            str(schedule_id),
            with_for_update=True,
        )
        if row is not None and str(row.status) == "starting":
            state = _premium_state_for_schedule(
                session,
                int(row.managed_account_id),
                current,
            )
            if not state.active:
                row.status = "skipped"
                row.status_reason = (
                    "Premium subscription expired before scheduled start. "
                    "Renew KES 250 via M-Pesa to schedule or trade again."
                )
                row.completed_at = current
                row.claimed_by = ""
                row.claim_expires_at = None
                row.updated_at = current
                blocked = True

                account = session.get(
                    ManagedAccount,
                    int(row.managed_account_id),
                    with_for_update=True,
                )
                if account is not None and bool(account.enabled):
                    account.enabled = False
                    account.execution_status = "manual_pause"
                    account.execution_status_reason = PREMIUM_REQUIRED_REASON[:160]
                    account.execution_status_updated_at = current
                    account.updated_at = current

    if blocked:
        try:
            scheduler._queue_private_schedule_alert(
                str(schedule_id),
                "skipped",
                "Premium expired before scheduled start",
            )
        except Exception:
            LOGGER.exception(
                "PREMIUM_SCHEDULE_SKIP_ALERT_FAILED schedule_id=%s",
                schedule_id,
            )
        LOGGER.info(
            "PREMIUM_SCHEDULE_START_BLOCKED schedule_id=%s reason=expired",
            schedule_id,
        )
        return False, "skipped"

    original = _ORIGINAL_SCHEDULE_APPLY
    if original is None:
        raise RuntimeError("Action 5 scheduler authority was not available")
    return original(str(schedule_id))


async def _premium_expiry_loop(stop_event: asyncio.Event) -> None:
    interval = max(
        1.0,
        float(os.getenv("PREMIUM_EXPIRY_SWEEP_INTERVAL_SECONDS", "5")),
    )
    LOGGER.warning(
        "PREMIUM_RENEWAL_ACTION6D_ACTIVE period_days=7 provider=lipana "
        "payment_method=mpesa exact_expiry=true sweep_seconds=%.2f "
        "schedule_start_gate=true open_settlement_preserved=true",
        interval,
    )
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run_premium_expiry_cycle)
        except Exception:
            LOGGER.exception("PREMIUM_EXPIRY_SWEEP_FAILED")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def install_premium_renewal_action6d(app: Any) -> None:
    global _INSTALLED, _ORIGINAL_SCHEDULE_APPLY
    if _INSTALLED:
        return

    _ORIGINAL_SCHEDULE_APPLY = scheduler._apply_schedule_strategy
    scheduler._apply_schedule_strategy = _premium_schedule_apply

    @app.get("/me/premium-access/renewal-status")
    def premium_renewal_status(request: Request) -> dict[str, Any]:
        customer, linked_count, _ = ensure_premium_customer(request)
        state = effective_access_state(customer)
        history = premium_access_period_history(
            base_api.DATABASE,
            str(customer.id),
            limit=200,
        )
        return {
            "authenticated": True,
            "premium": access_payload(
                state,
                linked_account_count=linked_count,
                checkout_ready=False,
            ),
            "renewal": renewal_reminder_payload(state),
            "renewal_count": max(0, len(history) - 1),
            "paid_period_count": len(history),
            "next_action": (
                "continue_using_premium"
                if state.active
                else "pay_kes_250_via_mpesa"
            ),
        }

    @app.get("/me/premium-access/renewal-history")
    def premium_renewal_history(
        request: Request,
        limit: int = 50,
    ) -> dict[str, Any]:
        customer, _, _ = ensure_premium_customer(request)
        safe_limit = max(1, min(200, int(limit)))
        items = premium_access_period_history(
            base_api.DATABASE,
            str(customer.id),
            limit=safe_limit,
        )
        return {
            "authenticated": True,
            "customer_id": str(customer.id),
            "items": items,
            "paid_period_count": len(items),
            "renewal_count": max(0, len(items) - 1),
        }

    @app.get("/me/premium-access/renew")
    def premium_renewal_instruction(request: Request) -> dict[str, Any]:
        customer, linked_count, _ = ensure_premium_customer(request)
        state = effective_access_state(customer)
        if state.active:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Premium is still active. M-Pesa renewal becomes available "
                    "after the exact current expiry time."
                ),
            )
        return {
            "success": True,
            "method": "mpesa",
            "provider": "lipana",
            "amount": 250.0,
            "currency": "KES",
            "period_days": 7,
            "action": "POST /me/premium-access/mpesa/stk-push",
            "premium": access_payload(
                state,
                linked_account_count=linked_count,
                checkout_ready=False,
            ),
        }

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def premium_renewal_lifespan(lifespan_app: Any):
        async with previous_lifespan(lifespan_app) as state:
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                _premium_expiry_loop(stop_event),
                name="action6d-premium-expiry",
            )
            app.state.premium_expiry_stop_event = stop_event
            app.state.premium_expiry_task = task
            try:
                yield state
            finally:
                stop_event.set()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    task.cancel()
                except asyncio.CancelledError:
                    pass

    app.router.lifespan_context = premium_renewal_lifespan
    app.state.premium_renewal_action6d_installed = True
    app.state.premium_renewal_mode = "manual_lipana_mpesa_after_exact_expiry"
    app.state.premium_expiry_authority = "current_period_end_exact_timestamp"
    _INSTALLED = True
