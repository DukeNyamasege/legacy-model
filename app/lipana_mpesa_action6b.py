from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.api as base_api
from app.models import utc_now
from app.premium_access_api import ensure_premium_customer
from app.premium_access_models import PremiumCustomer
from app.premium_access_service import (
    WEEKLY_PRICE_KES,
    access_payload,
    activate_weekly_access,
    effective_access_state,
)
from app.premium_payment_models import PremiumPaymentAttempt, PremiumWebhookEvent

try:
    from lipana import Lipana, LipanaError
except Exception:  # pragma: no cover - release images install the pinned SDK
    Lipana = None  # type: ignore[assignment]

    class LipanaError(Exception):
        pass


LOGGER = logging.getLogger("legacy_model.lipana_action6b")
_INSTALLED = False
PROVIDER = "lipana"
METHOD = "mpesa"
CURRENCY = "KES"
AMOUNT_MINOR = int(Decimal(str(WEEKLY_PRICE_KES)) * 100)
PENDING_TTL_MINUTES = 10
SUCCESS_STATUS_VALUES = {"success", "successful", "succeeded", "paid", "completed", "complete"}
FAILED_STATUS_VALUES = {"failed", "failure", "cancelled", "canceled", "rejected", "declined"}


class MpesaStkPushRequest(BaseModel):
    phone: str = Field(min_length=9, max_length=24)
    idempotency_key: str = Field(default="", max_length=96)


def _environment() -> str:
    value = str(os.getenv("LIPANA_ENVIRONMENT", "sandbox")).strip().lower()
    return value if value in {"sandbox", "production"} else "sandbox"


def _secret_key() -> str:
    return str(os.getenv("LIPANA_SECRET_KEY", "")).strip()


def _webhook_secret() -> str:
    return str(os.getenv("LIPANA_WEBHOOK_SECRET", "")).strip()


def _configured() -> bool:
    return bool(_secret_key() and _webhook_secret() and Lipana is not None)


def _client() -> Any:
    secret = _secret_key()
    if Lipana is None:
        raise HTTPException(status_code=503, detail="Lipana SDK is not installed.")
    if not secret:
        raise HTTPException(status_code=503, detail="Lipana secret key is not configured.")
    return Lipana(api_key=secret, environment=_environment())


def normalize_kenyan_mpesa_phone(value: str) -> str:
    compact = re.sub(r"[^0-9+]", "", str(value or "").strip())
    if compact.startswith("+254"):
        normalized = compact
    elif compact.startswith("254"):
        normalized = "+" + compact
    elif compact.startswith("0") and len(compact) == 10:
        normalized = "+254" + compact[1:]
    elif len(compact) == 9 and compact[:1] in {"7", "1"}:
        normalized = "+254" + compact
    else:
        raise ValueError("Enter a valid Kenyan M-Pesa number, for example 0712345678.")
    if not re.fullmatch(r"\+254[17]\d{8}", normalized):
        raise ValueError("Enter a valid Kenyan M-Pesa number, for example 0712345678.")
    return normalized


def _phone_hash(phone: str) -> str:
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def _mask_phone(phone: str) -> str:
    if len(phone) < 9:
        return "***"
    return f"{phone[:7]}***{phone[-3:]}"


def _merchant_reference() -> str:
    return f"DA-WEEKLY-{uuid4().hex[:20].upper()}"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        result = value.model_dump()
        return dict(result) if isinstance(result, dict) else {}
    if hasattr(value, "dict"):
        result = value.dict()
        return dict(result) if isinstance(result, dict) else {}
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        return dict(result) if isinstance(result, dict) else {}
    return {}


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _find_value(payload: Any, candidates: set[str]) -> Any:
    wanted = {_normalized_key(item) for item in candidates}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if _normalized_key(str(key)) in wanted and value not in {None, ""}:
                return value
        for preferred in ("data", "transaction", "payment", "result"):
            nested = payload.get(preferred)
            if isinstance(nested, (dict, list)):
                found = _find_value(nested, candidates)
                if found not in {None, ""}:
                    return found
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found = _find_value(value, candidates)
                if found not in {None, ""}:
                    return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_value(item, candidates)
            if found not in {None, ""}:
                return found
    return None


def _transaction_id(payload: Any) -> str:
    value = _find_value(payload, {"transactionId", "transaction_id", "transactionRef", "transaction_ref"})
    if value not in {None, ""}:
        return str(value).strip()
    generic = _find_value(payload, {"id"})
    generic_text = str(generic or "").strip()
    return generic_text if generic_text.lower().startswith("txn_") else ""


def _event_type(payload: dict[str, Any]) -> str:
    return str(payload.get("event") or payload.get("type") or "").strip().lower()


def _event_kind(event_type: str) -> str:
    normalized = str(event_type or "").strip().lower()
    payment_event = any(term in normalized for term in ("payment", "transaction", "stk"))
    if payment_event and normalized.endswith((".success", ".successful", ".succeeded", ".paid")):
        return "success"
    if payment_event and normalized.endswith((".failed", ".failure", ".cancelled", ".canceled", ".rejected", ".declined")):
        return "failed"
    return "other"


def _amount_minor(payload: Any) -> int | None:
    raw = _find_value(payload, {"amount", "paidAmount", "paid_amount"})
    if raw in {None, ""}:
        return None
    try:
        amount = Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return int(amount * 100)


def _currency(payload: Any) -> str:
    raw = _find_value(payload, {"currency", "currencyCode", "currency_code"})
    return str(raw or CURRENCY).strip().upper() or CURRENCY


def _provider_status(payload: Any) -> str:
    raw = _find_value(payload, {"status", "paymentStatus", "payment_status", "transactionStatus", "transaction_status"})
    return str(raw or "").strip().lower()


def _parse_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    text = str(value).strip()
    if re.fullmatch(r"\d{14}", text):
        try:
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        numeric = float(text)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        if numeric > 1_000_000_000:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _payment_time(provider_transaction: Any, webhook_payload: Any, fallback: datetime) -> tuple[datetime, str]:
    keys = {
        "paidAt",
        "paid_at",
        "completedAt",
        "completed_at",
        "confirmedAt",
        "confirmed_at",
        "transactionDate",
        "transaction_date",
        "timestamp",
    }
    provider_value = _find_value(provider_transaction, keys)
    parsed = _parse_datetime(provider_value)
    if parsed is not None:
        return parsed, "provider_transaction"
    webhook_value = _find_value(webhook_payload, keys)
    parsed = _parse_datetime(webhook_value)
    if parsed is not None:
        return parsed, "signed_webhook"
    return fallback, "webhook_received_fallback"


def _safe_error(exc: Exception) -> str:
    message = str(getattr(exc, "message", "") or str(exc) or type(exc).__name__).strip()
    return message[:500]


def _is_definitive_provider_error(exc: Exception) -> bool:
    for method_name in ("is_authentication_error", "is_validation_error"):
        method = getattr(exc, method_name, None)
        try:
            if callable(method) and bool(method()):
                return True
        except Exception:
            pass
    return False


def _attempt_payload(row: PremiumPaymentAttempt, *, reused: bool = False) -> dict[str, Any]:
    return {
        "id": row.id,
        "provider": row.provider,
        "payment_method": row.payment_method,
        "merchant_reference": row.merchant_reference,
        "provider_transaction_id": row.provider_transaction_id,
        "amount": float(Decimal(row.amount_minor) / Decimal(100)),
        "currency": row.currency,
        "phone": row.phone_masked,
        "status": row.status,
        "provider_status": row.provider_status or None,
        "requested_at": row.requested_at.isoformat(),
        "provider_accepted_at": row.provider_accepted_at.isoformat() if row.provider_accepted_at else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "expires_at": row.expires_at.isoformat(),
        "activated": bool(row.activated_at),
        "reused": bool(reused),
    }


def _latest_attempt(customer_id: str) -> PremiumPaymentAttempt | None:
    with base_api.DATABASE.session() as session:
        return session.scalar(
            select(PremiumPaymentAttempt)
            .where(PremiumPaymentAttempt.customer_id == str(customer_id))
            .order_by(PremiumPaymentAttempt.requested_at.desc())
            .limit(1)
        )


def _create_or_reuse_attempt(
    customer_id: str,
    *,
    phone: str,
    idempotency_key: str,
) -> tuple[PremiumPaymentAttempt, bool]:
    now = utc_now()
    clean_key = str(idempotency_key or "").strip() or None
    with base_api.DATABASE.session() as session:
        customer = session.get(PremiumCustomer, str(customer_id), with_for_update=True)
        if customer is None:
            raise ValueError("Premium customer was not found")

        if clean_key:
            existing = session.scalar(
                select(PremiumPaymentAttempt).where(
                    PremiumPaymentAttempt.customer_id == str(customer_id),
                    PremiumPaymentAttempt.idempotency_key == clean_key,
                )
            )
            if existing is not None:
                return existing, True

        existing_pending = session.scalar(
            select(PremiumPaymentAttempt)
            .where(
                PremiumPaymentAttempt.customer_id == str(customer_id),
                PremiumPaymentAttempt.status.in_(["initiating", "pending", "provider_uncertain"]),
                PremiumPaymentAttempt.expires_at > now,
            )
            .order_by(PremiumPaymentAttempt.requested_at.desc())
            .limit(1)
        )
        if existing_pending is not None:
            return existing_pending, True

        row = PremiumPaymentAttempt(
            id=str(uuid4()),
            customer_id=str(customer_id),
            provider=PROVIDER,
            payment_method=METHOD,
            merchant_reference=_merchant_reference(),
            provider_transaction_id=None,
            idempotency_key=clean_key,
            amount_minor=AMOUNT_MINOR,
            currency=CURRENCY,
            phone_hash=_phone_hash(phone),
            phone_masked=_mask_phone(phone),
            status="initiating",
            requested_at=now,
            expires_at=now + timedelta(minutes=PENDING_TTL_MINUTES),
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row, False


def _initiate_stk_push(client: Any, *, phone: str, merchant_reference: str) -> dict[str, Any]:
    method = client.transactions.initiate_stk_push
    kwargs: dict[str, Any] = {
        "phone": phone,
        "amount": float(WEEKLY_PRICE_KES),
    }
    try:
        parameters = inspect.signature(method).parameters
        supports_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if "account_reference" in parameters or supports_kwargs:
            kwargs["account_reference"] = merchant_reference
        if "transaction_desc" in parameters or supports_kwargs:
            kwargs["transaction_desc"] = "DerivAdmin Premium Weekly"
    except (TypeError, ValueError):
        pass
    return _mapping(method(**kwargs))


def _set_attempt_provider_result(
    attempt_id: str,
    *,
    status: str,
    provider_transaction_id: str | None = None,
    provider_status: str = "",
    failure_reason: str = "",
) -> PremiumPaymentAttempt:
    with base_api.DATABASE.session() as session:
        row = session.get(PremiumPaymentAttempt, str(attempt_id), with_for_update=True)
        if row is None:
            raise ValueError("Payment attempt was not found")
        now = utc_now()
        row.status = str(status)[:28]
        if provider_transaction_id:
            row.provider_transaction_id = str(provider_transaction_id)[:160]
        if provider_status:
            row.provider_status = str(provider_status)[:80]
        if failure_reason:
            row.failure_reason = str(failure_reason)[:2000]
        if status == "pending":
            row.provider_accepted_at = now
        if status in {"failed", "verification_failed"}:
            row.failed_at = now
        row.updated_at = now
        return row


def _verified_provider_transaction(client: Any, transaction_id: str) -> dict[str, Any]:
    retrieved = _mapping(client.transactions.retrieve(str(transaction_id)))
    retrieved_id = _transaction_id(retrieved)
    if retrieved_id and retrieved_id != str(transaction_id):
        raise ValueError("Lipana transaction reference did not match the initiated payment")
    amount_minor = _amount_minor(retrieved)
    if amount_minor is None:
        raise ValueError("Lipana transaction verification did not include an amount")
    if amount_minor != AMOUNT_MINOR:
        raise ValueError("Lipana transaction amount did not match KES 250")
    if _currency(retrieved) != CURRENCY:
        raise ValueError("Lipana transaction currency did not match KES")
    status = _provider_status(retrieved)
    if status and status not in SUCCESS_STATUS_VALUES:
        if status in FAILED_STATUS_VALUES:
            raise ValueError(f"Lipana transaction is {status}")
        raise RuntimeError(f"Lipana transaction is not final yet ({status})")
    return retrieved


def _record_webhook_event(
    *,
    digest: str,
    event_type: str,
    transaction_id: str,
) -> tuple[int, str]:
    def write_once() -> tuple[int, str]:
        with base_api.DATABASE.session() as session:
            existing = session.scalar(
                select(PremiumWebhookEvent).where(
                    PremiumWebhookEvent.event_digest == digest
                )
            )
            if existing is not None:
                return int(existing.id), str(existing.status)
            row = PremiumWebhookEvent(
                provider=PROVIDER,
                event_digest=digest,
                event_type=event_type[:80],
                provider_transaction_id=transaction_id[:160],
                status="received",
                received_at=utc_now(),
            )
            session.add(row)
            session.flush()
            return int(row.id), str(row.status)

    try:
        return write_once()
    except IntegrityError:
        return write_once()


def _update_webhook_event(
    event_id: int,
    *,
    status: str,
    detail: str = "",
    payment_attempt_id: str | None = None,
    processed: bool = False,
) -> None:
    with base_api.DATABASE.session() as session:
        row = session.get(PremiumWebhookEvent, int(event_id), with_for_update=True)
        if row is None:
            return
        row.status = str(status)[:32]
        row.detail = str(detail or "")[:2000]
        if payment_attempt_id:
            row.payment_attempt_id = str(payment_attempt_id)
        if processed:
            row.processed_at = utc_now()


def _attempt_by_transaction_id(transaction_id: str) -> PremiumPaymentAttempt | None:
    with base_api.DATABASE.session() as session:
        return session.scalar(
            select(PremiumPaymentAttempt).where(
                PremiumPaymentAttempt.provider == PROVIDER,
                PremiumPaymentAttempt.provider_transaction_id == str(transaction_id),
            )
        )


def _mark_failed_from_webhook(attempt_id: str, provider_status: str) -> None:
    with base_api.DATABASE.session() as session:
        row = session.get(PremiumPaymentAttempt, str(attempt_id), with_for_update=True)
        if row is None or row.activated_at is not None:
            return
        now = utc_now()
        row.status = "failed"
        row.provider_status = str(provider_status or "failed")[:80]
        row.failure_reason = "M-Pesa payment was not completed"
        row.failed_at = now
        row.updated_at = now


def _activate_verified_attempt(
    attempt: PremiumPaymentAttempt,
    *,
    provider_transaction: dict[str, Any],
    webhook_payload: dict[str, Any],
    received_at: datetime,
) -> dict[str, Any]:
    paid_at, time_source = _payment_time(provider_transaction, webhook_payload, received_at)
    state = activate_weekly_access(
        base_api.DATABASE,
        str(attempt.customer_id),
        paid_at=paid_at,
        provider=PROVIDER,
        payment_reference=str(attempt.provider_transaction_id or ""),
        auto_renew_enabled=False,
        renewal_preference="prompt_again",
    )
    with base_api.DATABASE.session() as session:
        row = session.get(PremiumPaymentAttempt, str(attempt.id), with_for_update=True)
        if row is None:
            raise ValueError("Payment attempt disappeared before activation")
        if row.activated_at is None:
            row.status = "success"
            row.provider_status = _provider_status(provider_transaction) or "success"
            row.confirmed_at = paid_at
            row.payment_time_source = time_source
            row.activated_at = utc_now()
            row.failure_reason = ""
            row.updated_at = utc_now()
    return access_payload(state, linked_account_count=0, checkout_ready=True)


def install_lipana_mpesa_action6b(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    @app.get("/me/premium-access/payment-options")
    def premium_payment_options(request: Request) -> dict[str, Any]:
        customer, linked_count, _ = ensure_premium_customer(request)
        state = effective_access_state(customer)
        public_origin = str(os.getenv("PUBLIC_ORIGIN", "https://derivadmin.site")).rstrip("/")
        return {
            "authenticated": True,
            "premium": access_payload(
                state,
                linked_account_count=linked_count,
                checkout_ready=_configured(),
            ),
            "methods": {
                "mpesa": {
                    "available": _configured(),
                    "provider": PROVIDER,
                    "amount": float(WEEKLY_PRICE_KES),
                    "currency": CURRENCY,
                    "environment": _environment(),
                    "renewal_mode": "prompt_again_after_expiry",
                },
                "card": {
                    "available": False,
                    "provider": "flutterwave",
                    "phase": "action6c",
                },
            },
            "lipana": {
                "secret_key_configured": bool(_secret_key()),
                "webhook_secret_configured": bool(_webhook_secret()),
                "webhook_url": f"{public_origin}/api/webhooks/lipana",
            },
        }

    @app.post("/me/premium-access/mpesa/stk-push")
    def initiate_mpesa_stk_push(
        request: Request,
        body: MpesaStkPushRequest,
    ) -> dict[str, Any]:
        try:
            phone = normalize_kenyan_mpesa_phone(body.phone)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        customer, linked_count, account = ensure_premium_customer(request)
        state = effective_access_state(customer)
        if state.active:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Premium access is already active until "
                    f"{state.current_period_end.isoformat() if state.current_period_end else 'the current expiry'}"
                ),
            )
        if not _configured():
            raise HTTPException(
                status_code=503,
                detail="M-Pesa checkout is not configured yet.",
            )

        try:
            attempt, reused = _create_or_reuse_attempt(
                str(customer.id),
                phone=phone,
                idempotency_key=body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if reused or attempt.status != "initiating":
            return {
                "success": attempt.status in {"pending", "success"},
                "message": (
                    "An M-Pesa payment request is already pending. Complete it on your phone."
                    if attempt.status in {"initiating", "pending", "provider_uncertain"}
                    else "This payment request has already been processed."
                ),
                "payment": _attempt_payload(attempt, reused=True),
                "premium": access_payload(
                    state,
                    linked_account_count=linked_count,
                    checkout_ready=True,
                ),
            }

        client = _client()
        try:
            provider_response = _initiate_stk_push(
                client,
                phone=phone,
                merchant_reference=attempt.merchant_reference,
            )
            transaction_id = _transaction_id(provider_response)
            if not transaction_id:
                row = _set_attempt_provider_result(
                    attempt.id,
                    status="provider_uncertain",
                    failure_reason="Lipana accepted the request without returning a transaction ID",
                )
                raise HTTPException(
                    status_code=502,
                    detail={
                        "message": "Lipana did not return a transaction reference. Do not retry immediately; wait for payment status.",
                        "payment": _attempt_payload(row),
                    },
                )
            row = _set_attempt_provider_result(
                attempt.id,
                status="pending",
                provider_transaction_id=transaction_id,
                provider_status=_provider_status(provider_response) or "pending",
            )
        except HTTPException:
            raise
        except Exception as exc:
            definitive = _is_definitive_provider_error(exc)
            row = _set_attempt_provider_result(
                attempt.id,
                status="failed" if definitive else "provider_uncertain",
                failure_reason=_safe_error(exc),
            )
            LOGGER.warning(
                "LIPANA_STK_INITIATION_FAILED managed_account_id=%s attempt_id=%s definitive=%s error_type=%s",
                int(account.get("id") or 0),
                attempt.id,
                definitive,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "message": (
                        "M-Pesa request could not be started."
                        if definitive
                        else "M-Pesa request status is uncertain. Do not retry immediately."
                    ),
                    "payment": _attempt_payload(row),
                },
            ) from exc

        try:
            base_api.REPOSITORY.audit(
                "PREMIUM_MPESA_STK_PUSH_CREATED",
                str(account.get("account_id_masked") or "premium"),
                request.client.host if request.client else "unknown",
                {
                    "payment_attempt_id": row.id,
                    "provider": PROVIDER,
                    "provider_transaction_id": transaction_id,
                    "amount_minor": AMOUNT_MINOR,
                    "currency": CURRENCY,
                    "phone_masked": row.phone_masked,
                },
            )
        except Exception:
            LOGGER.exception("PREMIUM_MPESA_STK_AUDIT_FAILED")

        return {
            "success": True,
            "message": "M-Pesa prompt sent. Enter your PIN on the phone to complete KES 250 payment.",
            "payment": _attempt_payload(row),
            "premium": access_payload(
                state,
                linked_account_count=linked_count,
                checkout_ready=True,
            ),
        }

    @app.get("/me/premium-access/mpesa/payments/latest")
    def latest_mpesa_payment(request: Request) -> dict[str, Any]:
        customer, linked_count, _ = ensure_premium_customer(request)
        row = _latest_attempt(str(customer.id))
        state = effective_access_state(customer)
        return {
            "success": True,
            "payment": _attempt_payload(row) if row is not None else None,
            "premium": access_payload(
                state,
                linked_account_count=linked_count,
                checkout_ready=_configured(),
            ),
        }

    @app.get("/me/premium-access/mpesa/payments/{attempt_id}")
    def get_mpesa_payment(request: Request, attempt_id: str) -> dict[str, Any]:
        customer, linked_count, _ = ensure_premium_customer(request)
        with base_api.DATABASE.session() as session:
            row = session.get(PremiumPaymentAttempt, str(attempt_id))
            if row is None or str(row.customer_id) != str(customer.id):
                raise HTTPException(status_code=404, detail="Payment attempt not found")
            payment = _attempt_payload(row)
        state = effective_access_state(customer)
        return {
            "success": True,
            "payment": payment,
            "premium": access_payload(
                state,
                linked_account_count=linked_count,
                checkout_ready=_configured(),
            ),
        }

    @app.post("/webhooks/lipana")
    async def lipana_webhook(request: Request) -> dict[str, Any]:
        signature = str(request.headers.get("x-lipana-signature") or "").strip()
        secret = _webhook_secret()
        if not secret or not _secret_key():
            raise HTTPException(status_code=503, detail="Lipana webhook verification is not configured")
        if not signature:
            raise HTTPException(status_code=401, detail="Missing Lipana webhook signature")

        raw_body = await request.body()
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid webhook JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid webhook payload")

        client = _client()
        try:
            valid = bool(client.webhooks.verify(payload, signature, secret))
        except Exception as exc:
            LOGGER.warning("LIPANA_WEBHOOK_SIGNATURE_CHECK_FAILED error_type=%s", type(exc).__name__)
            raise HTTPException(status_code=401, detail="Invalid Lipana webhook signature") from exc
        if not valid:
            raise HTTPException(status_code=401, detail="Invalid Lipana webhook signature")

        received_at = utc_now()
        event_type = _event_type(payload)
        transaction_id = _transaction_id(payload)
        digest = hashlib.sha256(raw_body).hexdigest()
        event_id, prior_status = _record_webhook_event(
            digest=digest,
            event_type=event_type,
            transaction_id=transaction_id,
        )
        if prior_status in {"processed", "ignored", "rejected"}:
            return {"received": True, "duplicate": True}

        kind = _event_kind(event_type)
        if kind == "other":
            _update_webhook_event(
                event_id,
                status="ignored",
                detail="Webhook event is not a premium M-Pesa terminal event",
                processed=True,
            )
            return {"received": True, "ignored": True}
        if not transaction_id:
            _update_webhook_event(
                event_id,
                status="ignored",
                detail="Signed webhook did not contain a transaction reference",
                processed=True,
            )
            return {"received": True, "ignored": True}

        attempt = _attempt_by_transaction_id(transaction_id)
        if attempt is None:
            _update_webhook_event(
                event_id,
                status="ignored",
                detail="Transaction does not belong to a DerivAdmin premium payment attempt",
                processed=True,
            )
            return {"received": True, "ignored": True}

        _update_webhook_event(
            event_id,
            status="received",
            payment_attempt_id=attempt.id,
        )

        if kind == "failed":
            _mark_failed_from_webhook(attempt.id, event_type)
            _update_webhook_event(
                event_id,
                status="processed",
                detail="Payment failure recorded; premium access was not extended",
                payment_attempt_id=attempt.id,
                processed=True,
            )
            return {"received": True, "payment_status": "failed"}

        if attempt.activated_at is not None:
            _update_webhook_event(
                event_id,
                status="processed",
                detail="Payment had already activated the same seven-day entitlement",
                payment_attempt_id=attempt.id,
                processed=True,
            )
            return {"received": True, "payment_status": "success", "duplicate": True}

        try:
            provider_transaction = _verified_provider_transaction(client, transaction_id)
        except RuntimeError as exc:
            _update_webhook_event(
                event_id,
                status="verification_pending",
                detail=str(exc),
                payment_attempt_id=attempt.id,
            )
            raise HTTPException(status_code=503, detail="Lipana transaction is not final yet") from exc
        except Exception as exc:
            _set_attempt_provider_result(
                attempt.id,
                status="verification_failed",
                provider_transaction_id=transaction_id,
                failure_reason=str(exc),
            )
            _update_webhook_event(
                event_id,
                status="rejected",
                detail=str(exc),
                payment_attempt_id=attempt.id,
                processed=True,
            )
            LOGGER.error(
                "LIPANA_PAYMENT_VERIFICATION_REJECTED attempt_id=%s transaction_id=%s reason=%s",
                attempt.id,
                transaction_id,
                str(exc)[:300],
            )
            return {"received": True, "payment_status": "verification_failed"}

        premium = _activate_verified_attempt(
            attempt,
            provider_transaction=provider_transaction,
            webhook_payload=payload,
            received_at=received_at,
        )
        _update_webhook_event(
            event_id,
            status="processed",
            detail="Verified KES 250 payment activated exactly seven days",
            payment_attempt_id=attempt.id,
            processed=True,
        )
        try:
            base_api.REPOSITORY.audit(
                "PREMIUM_MPESA_PAYMENT_VERIFIED",
                "lipana-webhook",
                request.client.host if request.client else "provider",
                {
                    "payment_attempt_id": attempt.id,
                    "provider_transaction_id": transaction_id,
                    "customer_id": str(attempt.customer_id),
                    "amount_minor": AMOUNT_MINOR,
                    "currency": CURRENCY,
                    "entitlement_days": 7,
                },
            )
        except Exception:
            LOGGER.exception("PREMIUM_MPESA_WEBHOOK_AUDIT_FAILED")
        return {
            "received": True,
            "payment_status": "success",
            "premium_active": bool(premium.get("active")),
            "expires_at": premium.get("expires_at"),
        }

    app.state.lipana_mpesa_action6b_installed = True
    app.state.lipana_mpesa_environment = _environment()
    app.state.lipana_mpesa_configured = _configured()
    LOGGER.warning(
        "LIPANA_MPESA_ACTION6B_ACTIVE amount_kes=250 webhook_signature=true "
        "server_transaction_reverification=true raw_phone_persisted=false renewal=prompt_again"
    )
    _INSTALLED = True
