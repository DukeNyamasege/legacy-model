from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

import app.api as base_api
from app.models import ManagedAccount, RuntimePreference, utc_now
from app.token_store import decrypt_auth_payload


DEFAULT_TIMEZONE = "Africa/Nairobi"
PREFERENCE_PREFIX = "automation_timezone:"
_INSTALLED = False


class AutomationTimezoneRequest(BaseModel):
    timezone: str = Field(min_length=1, max_length=80)


def _preference_key(account_id: str) -> str:
    digest = hashlib.sha256(str(account_id).strip().upper().encode("utf-8")).hexdigest()[:48]
    return f"{PREFERENCE_PREFIX}{digest}"


def _current_context(request: Request) -> tuple[dict[str, Any], list[str]]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Log in with Deriv first.")

    current_account_id = str(account.get("account_id") or "").strip()
    managed_id = int(account.get("id") or 0)
    linked_ids: set[str] = {current_account_id} if current_account_id else set()
    login_identity = ""

    if managed_id > 0:
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id)
            if row is not None:
                try:
                    payload = decrypt_auth_payload(
                        row.token_secret,
                        base_api.CONFIG.deriv.token_encryption_key,
                    )
                    login_identity = base_api.login_identity_from_payload(payload)
                except Exception:
                    login_identity = ""

    # OAuth tokens can rotate at a later login, so the durable preference is
    # mirrored to each linked Options account ID. The OAuth identity is used
    # only to discover today's DOT/ROT cohort, never as the durable key.
    if login_identity:
        for row in base_api.REPOSITORY.list_managed_accounts():
            try:
                payload = decrypt_auth_payload(
                    row.token_secret,
                    base_api.CONFIG.deriv.token_encryption_key,
                )
            except Exception:
                continue
            if base_api.login_identity_from_payload(payload) != login_identity:
                continue
            account_id = str(payload.get("account_id") or "").strip()
            if account_id:
                linked_ids.add(account_id)

    if not linked_ids:
        linked_ids.add(str(managed_id or "session"))
    return account, sorted(linked_ids)


def _validated_timezone(value: str) -> str:
    name = str(value or "").strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Choose a valid IANA timezone.") from exc
    return name


def _timezone_meta(name: str) -> dict[str, Any]:
    zone = ZoneInfo(name)
    now = datetime.now(zone)
    offset = now.utcoffset()
    seconds = int(offset.total_seconds()) if offset is not None else 0
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return {
        "timezone": name,
        "abbreviation": now.tzname() or name,
        "utc_offset": f"UTC{sign}{hours:02d}:{minutes:02d}",
    }


def _read_preference(account_ids: list[str]) -> tuple[str, bool]:
    with base_api.DATABASE.session() as session:
        for account_id in account_ids:
            row = session.get(RuntimePreference, _preference_key(account_id))
            saved = str(row.preference_value or "").strip() if row is not None else ""
            if not saved:
                continue
            try:
                return _validated_timezone(saved), True
            except HTTPException:
                continue
    return DEFAULT_TIMEZONE, False


def _write_preference(account_ids: list[str], timezone_name: str) -> None:
    with base_api.DATABASE.session() as session:
        for account_id in account_ids:
            key = _preference_key(account_id)
            row = session.get(RuntimePreference, key)
            if row is None:
                session.add(
                    RuntimePreference(
                        preference_key=key,
                        preference_value=timezone_name,
                    )
                )
            else:
                row.preference_value = timezone_name
                row.updated_at = utc_now()


def install_automation_preferences_api(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    @app.get("/me/automation-preferences")
    def get_automation_preferences(request: Request) -> dict[str, Any]:
        account, linked_account_ids = _current_context(request)
        timezone_name, configured = _read_preference(linked_account_ids)
        return {
            "authenticated": True,
            "configured": configured,
            "requires_timezone_onboarding": not configured,
            "default_timezone": DEFAULT_TIMEZONE,
            "scope": "linked_options_accounts",
            "managed_account_id": int(account.get("id") or 0),
            "linked_account_count": len(linked_account_ids),
            **_timezone_meta(timezone_name),
        }

    @app.post("/me/automation-preferences/timezone")
    def set_automation_timezone(
        request: Request,
        body: AutomationTimezoneRequest,
    ) -> dict[str, Any]:
        account, linked_account_ids = _current_context(request)
        timezone_name = _validated_timezone(body.timezone)
        _write_preference(linked_account_ids, timezone_name)

        try:
            base_api.REPOSITORY.audit(
                "AUTOMATION_TIMEZONE_CHANGED",
                "personal_dashboard",
                request.client.host if request.client else "unknown",
                {
                    "managed_account_id": int(account.get("id") or 0),
                    "timezone": timezone_name,
                    "scope": "linked_options_accounts",
                    "linked_account_count": len(linked_account_ids),
                },
            )
        except Exception:
            base_api.LOGGER.exception("AUTOMATION_TIMEZONE_AUDIT_FAILED")

        return {
            "success": True,
            "configured": True,
            "requires_timezone_onboarding": False,
            "default_timezone": DEFAULT_TIMEZONE,
            "scope": "linked_options_accounts",
            "linked_account_count": len(linked_account_ids),
            **_timezone_meta(timezone_name),
        }

    app.state.automation_preferences_action4_installed = True
    app.state.automation_default_timezone = DEFAULT_TIMEZONE
    _INSTALLED = True
