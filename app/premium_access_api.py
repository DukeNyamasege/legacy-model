from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.api as base_api
from app.models import ManagedAccount, utc_now
from app.premium_access_models import PremiumCustomer, PremiumCustomerAccount
from app.premium_access_service import (
    access_payload,
    effective_access_state,
    premium_account_hash,
    premium_identity_fingerprint,
)
from app.token_store import decrypt_auth_payload


LOGGER = logging.getLogger("legacy_model.premium_access_action6a")
_INSTALLED = False

SAFE_UNPAID_MUTATIONS = {
    "/me/switch-account",
    "/me/automation-preferences/timezone",
    "/me/stop-trading",
    "/me/pause-trading",
}


@dataclass(frozen=True)
class LinkedPremiumAccount:
    managed_account_id: int
    account_id: str
    account_hash: str
    account_masked: str
    account_type: str


def _enforcement_enabled() -> bool:
    value = str(os.getenv("PREMIUM_ACCESS_ENFORCEMENT", "true")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def premium_write_requires_access(method: str, path: str) -> bool:
    verb = str(method or "GET").upper()
    route = str(path or "")
    if verb not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if not route.startswith("/me/"):
        return False
    if route.startswith("/me/premium-access"):
        return False
    if route in SAFE_UNPAID_MUTATIONS:
        return False
    if route.startswith("/me/automation-schedules/") and route.endswith("/cancel"):
        return False
    return True


def _selected_managed_payload(
    account: dict[str, Any],
) -> tuple[ManagedAccount | None, dict[str, Any]]:
    managed_id = int(account.get("id") or 0)
    if managed_id <= 0:
        return None, {}
    with base_api.DATABASE.session() as session:
        row = session.get(ManagedAccount, managed_id)
        if row is None:
            return None, {}
        try:
            payload = decrypt_auth_payload(
                row.token_secret,
                base_api.CONFIG.deriv.token_encryption_key,
            )
        except Exception:
            return row, {}
        return row, payload


def _account_context(
    row: ManagedAccount,
    payload: dict[str, Any],
) -> LinkedPremiumAccount | None:
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        return None
    return LinkedPremiumAccount(
        managed_account_id=int(row.id),
        account_id=account_id,
        account_hash=premium_account_hash(account_id),
        account_masked=base_api.mask_account_id(account_id),
        account_type=base_api.account_type_from_payload(payload),
    )


def _discover_linked_accounts(account: dict[str, Any]) -> list[LinkedPremiumAccount]:
    """Discover one trader's DOT/ROT cohort only when no durable mapping exists yet."""

    selected_row, selected_payload = _selected_managed_payload(account)
    if selected_row is None:
        return []
    selected = _account_context(selected_row, selected_payload)
    if selected is None:
        return []

    identity = base_api.login_identity_from_payload(selected_payload)
    if not identity:
        return [selected]

    linked: dict[str, LinkedPremiumAccount] = {selected.account_hash: selected}
    for row in base_api.REPOSITORY.list_managed_accounts():
        if int(row.id) == int(selected_row.id):
            continue
        try:
            payload = decrypt_auth_payload(
                row.token_secret,
                base_api.CONFIG.deriv.token_encryption_key,
            )
        except Exception:
            continue
        if base_api.login_identity_from_payload(payload) != identity:
            continue
        context = _account_context(row, payload)
        if context is not None:
            linked[context.account_hash] = context
    return sorted(linked.values(), key=lambda item: (item.account_type, item.account_hash))


def _customer_by_fast_mapping(
    managed_account_id: int,
) -> tuple[PremiumCustomer | None, int]:
    with base_api.DATABASE.session() as session:
        mapping = session.scalar(
            select(PremiumCustomerAccount).where(
                PremiumCustomerAccount.managed_account_id == int(managed_account_id)
            )
        )
        if mapping is None:
            return None, 0
        customer = session.get(PremiumCustomer, str(mapping.customer_id))
        count = len(
            list(
                session.scalars(
                    select(PremiumCustomerAccount).where(
                        PremiumCustomerAccount.customer_id == str(mapping.customer_id)
                    )
                ).all()
            )
        )
        return customer, count


def _persist_customer_links(
    linked: list[LinkedPremiumAccount],
) -> tuple[PremiumCustomer, int]:
    account_ids = [item.account_id for item in linked]
    fingerprint = premium_identity_fingerprint(account_ids)
    deterministic_customer_id = str(
        uuid5(NAMESPACE_URL, f"https://derivadmin.site/premium/{fingerprint}")
    )
    now = utc_now()

    def write_once() -> tuple[PremiumCustomer, int]:
        with base_api.DATABASE.session() as session:
            mappings = list(
                session.scalars(
                    select(PremiumCustomerAccount).where(
                        PremiumCustomerAccount.account_hash.in_(
                            [item.account_hash for item in linked]
                        )
                    )
                ).all()
            )
            customer_ids = {str(item.customer_id) for item in mappings}
            if len(customer_ids) > 1:
                raise RuntimeError(
                    "Linked DOT/ROT accounts resolve to conflicting premium identities"
                )

            customer_id = next(iter(customer_ids), deterministic_customer_id)
            customer = session.get(PremiumCustomer, customer_id)
            if customer is None:
                customer = session.scalar(
                    select(PremiumCustomer).where(
                        PremiumCustomer.identity_fingerprint == fingerprint
                    )
                )
            if customer is None:
                customer = PremiumCustomer(
                    id=deterministic_customer_id,
                    identity_fingerprint=fingerprint,
                    status="unpaid",
                    plan_code="weekly_access",
                    renewal_preference="prompt_again",
                    auto_renew_enabled=False,
                    renewal_provider="lipana",
                    created_at=now,
                    updated_at=now,
                )
                session.add(customer)
                session.flush()

            by_hash = {str(item.account_hash): item for item in mappings}
            for item in linked:
                mapping = by_hash.get(item.account_hash)
                if mapping is None:
                    mapping = PremiumCustomerAccount(
                        customer_id=str(customer.id),
                        managed_account_id=int(item.managed_account_id),
                        account_hash=item.account_hash,
                        account_masked=item.account_masked,
                        account_type=item.account_type,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(mapping)
                else:
                    if str(mapping.customer_id) != str(customer.id):
                        raise RuntimeError(
                            "Linked account already belongs to another premium identity"
                        )
                    mapping.managed_account_id = int(item.managed_account_id)
                    mapping.account_masked = item.account_masked
                    mapping.account_type = item.account_type
                    mapping.updated_at = now

            session.flush()
            total = len(
                list(
                    session.scalars(
                        select(PremiumCustomerAccount).where(
                            PremiumCustomerAccount.customer_id == str(customer.id)
                        )
                    ).all()
                )
            )
            return customer, total

    try:
        return write_once()
    except IntegrityError:
        return write_once()


def ensure_premium_customer(
    request: Request,
) -> tuple[PremiumCustomer, int, dict[str, Any]]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Log in with Deriv first.")
    if account.get("local_dev_preview"):
        raise HTTPException(
            status_code=409,
            detail="Premium identity is not created for local preview accounts.",
        )

    managed_id = int(account.get("id") or 0)
    customer, linked_count = _customer_by_fast_mapping(managed_id)
    if customer is not None:
        return customer, linked_count, account

    linked = _discover_linked_accounts(account)
    if not linked:
        raise HTTPException(
            status_code=409,
            detail="Linked Deriv Options accounts could not be resolved.",
        )
    try:
        customer, linked_count = _persist_customer_links(linked)
    except RuntimeError as exc:
        LOGGER.exception("PREMIUM_IDENTITY_CONFLICT managed_account_id=%s", managed_id)
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        base_api.REPOSITORY.audit(
            "PREMIUM_CUSTOMER_RESOLVED",
            str(account.get("account_id_masked") or "personal_dashboard"),
            request.client.host if request.client else "unknown",
            {
                "premium_customer_id": str(customer.id),
                "managed_account_id": managed_id,
                "linked_account_count": linked_count,
                "raw_account_ids_stored": False,
            },
        )
    except Exception:
        LOGGER.exception("PREMIUM_CUSTOMER_AUDIT_FAILED")
    return customer, linked_count, account


def _premium_payload_for_request(request: Request) -> dict[str, Any]:
    account = base_api.get_current_account(request)
    if not account:
        return {"authenticated": False}
    if account.get("local_dev_preview"):
        return {
            "authenticated": True,
            "local_dev_preview": True,
            **access_payload(
                effective_access_state(None),
                linked_account_count=1,
                checkout_ready=False,
            ),
            "active": True,
            "status": "local_preview_bypass",
            "premium_required": False,
        }

    customer, linked_count, _ = ensure_premium_customer(request)
    state = effective_access_state(customer)
    if state.status == "expired" and str(customer.status).lower() == "active":
        with base_api.DATABASE.session() as session:
            locked = session.get(PremiumCustomer, str(customer.id), with_for_update=True)
            if locked is not None and effective_access_state(locked).status == "expired":
                locked.status = "expired"
                locked.updated_at = utc_now()
    return {
        "authenticated": True,
        "scope": "linked_options_accounts",
        **access_payload(
            state,
            linked_account_count=linked_count,
            checkout_ready=False,
        ),
        "payment_setup_phase": "action6d_lipana_mpesa_weekly_renewal",
    }


def install_premium_access_action6a(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    @app.get("/me/premium-access")
    def get_premium_access(request: Request) -> dict[str, Any]:
        return _premium_payload_for_request(request)

    @app.middleware("http")
    async def enforce_premium_access(request: Request, call_next):
        if not _enforcement_enabled() or not premium_write_requires_access(
            request.method,
            request.url.path,
        ):
            return await call_next(request)

        account = base_api.get_current_account(request)
        if not account:
            return await call_next(request)
        if account.get("local_dev_preview"):
            return await call_next(request)

        try:
            customer, linked_count, _ = ensure_premium_customer(request)
            state = effective_access_state(customer)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
        if state.active:
            return await call_next(request)

        payload = access_payload(
            state,
            linked_account_count=linked_count,
            checkout_ready=False,
        )
        LOGGER.info(
            "PREMIUM_ACCESS_DENIED method=%s path=%s managed_account_id=%s status=%s",
            request.method,
            request.url.path,
            int(account.get("id") or 0),
            state.status,
        )
        return JSONResponse(
            status_code=402,
            content={
                "detail": (
                    "DerivAdmin is available for premium use only. "
                    "Pay KES 250 via M-Pesa for 7 days of access."
                ),
                "code": "PREMIUM_SUBSCRIPTION_REQUIRED",
                "premium": payload,
            },
        )

    app.state.premium_access_action6a_installed = True
    app.state.premium_access_scope = "linked_options_accounts"
    app.state.premium_access_period_days = 7
    app.state.premium_access_enforcement = _enforcement_enabled()
    LOGGER.warning(
        "PREMIUM_ACCESS_ACTION6A_ACTIVE scope=linked_dot_rot period_days=7 "
        "price_kes=250 payment_method=mpesa backend_gate=true"
    )
    _INSTALLED = True
