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


def _current_identity(request: Request) -> tuple[dict[str, Any], str]:
    account = base_api.get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Log in with Deriv first.")

    identity = ""
    managed_id = int(account.get("id") or 0)
    if managed_id > 0:
        with base_api.DATABASE.session() as session:
            row = session.get(ManagedAccount, managed_id)
            if row is not None:
                try:
                    payload = decrypt_auth_payload(
                        row.token_secret,
                        base_api.CONFIG.deriv.token_encryption_key,
                    )
                    identity = base_api.login_identity_from_payload(payload)
                except Exception:
                    identity = ""

    if not identity:
        account_id = str(account.get("account_id") or "").strip()
        identity = f"account:{account_id or managed_id or 'session'}"
    return account, identity


def _preference_key(identity: str) -> str:
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:48]
    return f"{PREFERENCE_PREFIX}{digest}"


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


def _read_preference(identity: str) -> tuple[str, bool]:
    key = _preference_key(identity)
    with base_api.DATABASE.session() as session:
        row = session.get(RuntimePreference, key)
        saved = str(row.preference_value or "").strip() if row is not None else ""
    if saved:
        try:
            return _validated_timezone(saved), True
        except HTTPException:
            pass
    return DEFAULT_TIMEZONE, False


def install_automation_preferences_api(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    @app.get("/me/automation-preferences")
    def get_automation_preferences(request: Request) -> dict[str, Any]:
        account, identity = _current_identity(request)
        timezone_name, configured = _read_preference(identity)
        meta = _timezone_meta(timezone_name)
        return {
            "authenticated": True,
            "configured": configured,
            "requires_timezone_onboarding": not configured,
            "default_timezone": DEFAULT_TIMEZONE,
            "scope": "oauth_identity",
            "managed_account_id": int(account.get("id") or 0),
            **meta,
        }

    @app.post("/me/automation-preferences/timezone")
    def set_automation_timezone(
        request: Request,
        body: AutomationTimezoneRequest,
    ) -> dict[str, Any]:
        account, identity = _current_identity(request)
        timezone_name = _validated_timezone(body.timezone)
        key = _preference_key(identity)
        with base_api.DATABASE.session() as session:
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

        try:
            base_api.REPOSITORY.audit(
                "AUTOMATION_TIMEZONE_CHANGED",
                "personal_dashboard",
                request.client.host if request.client else "unknown",
                {
                    "managed_account_id": int(account.get("id") or 0),
                    "timezone": timezone_name,
                    "scope": "oauth_identity",
                },
            )
        except Exception:
            base_api.LOGGER.exception("AUTOMATION_TIMEZONE_AUDIT_FAILED")

        return {
            "success": True,
            "configured": True,
            "requires_timezone_onboarding": False,
            "default_timezone": DEFAULT_TIMEZONE,
            "scope": "oauth_identity",
            **_timezone_meta(timezone_name),
        }

    app.state.automation_preferences_action4_installed = True
    app.state.automation_default_timezone = DEFAULT_TIMEZONE
    _INSTALLED = True
