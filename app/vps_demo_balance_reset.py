from __future__ import annotations

"""Secure control-plane bridge to Deriv's official demo-balance reset REST API.

The browser never receives the user's long-lived OAuth/PAT credential. This route
uses the target linked ManagedAccount credential server-side only for the
low-frequency account-management call, while live tick/proposal/BUY execution
remains browser ↔ Deriv direct.
"""

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import HTTPException, Request
from pydantic import BaseModel

import app.api as base_api
from app.deriv.http import deriv_headers
from app.token_store import decrypt_auth_payload
from app.vps_direct_execution_api import _trade_credential

_INSTALLED = False
DEFAULT_DEMO_BALANCE = 10000.0


class DemoBalanceResetRequest(BaseModel):
    managed_account_id: int | None = None


def _provider_error(exc: HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors:
            return str((errors[0] or {}).get("message") or "Deriv rejected the balance reset")
    except Exception:
        pass
    return "Deriv rejected the balance reset"


def _managed_payload(managed_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    row = base_api.REPOSITORY.managed_account(int(managed_id))
    if not row:
        raise HTTPException(status_code=404, detail="Managed account was not found")
    try:
        payload = decrypt_auth_payload(
            row["token_secret"],
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Trading credential is unreadable") from exc
    return row, payload


def _identity(payload: dict[str, Any]) -> str:
    account_id = str(payload.get("account_id") or "").strip()
    return base_api.login_identity_from_payload(payload) or f"account:{account_id}"


def install_vps_demo_balance_reset(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    @app.post("/me/reset-demo-balance")
    def reset_demo_balance(request: Request, body: DemoBalanceResetRequest) -> dict[str, Any]:
        current = base_api.get_current_account(request)
        if not current:
            raise HTTPException(status_code=401, detail="Not authenticated")

        current_id = int(current["id"])
        _current_row, current_payload = _managed_payload(current_id)
        target_id = int(body.managed_account_id or current_id)
        _target_row, payload = _managed_payload(target_id)

        # A caller may reset a linked demo row without first switching the selected
        # account, but never an arbitrary ManagedAccount belonging to another login.
        if _identity(payload) != _identity(current_payload):
            raise HTTPException(status_code=404, detail="That demo account is not linked to this Deriv login")

        account_id = str(payload.get("account_id") or "").strip()
        account_type = base_api.account_type_from_payload(payload)
        if account_type != "demo":
            raise HTTPException(status_code=400, detail="Only Deriv demo accounts can have their balance reset")
        if not account_id:
            raise HTTPException(status_code=409, detail="Deriv demo account ID is unavailable")

        token = _trade_credential(payload)
        base_url = str(base_api.CONFIG.deriv.rest_base_url or "https://api.derivws.com").rstrip("/")
        url = f"{base_url}/trading/v1/options/accounts/{account_id}/reset-demo-balance"
        provider_request = UrlRequest(
            url,
            data=b"",
            method="POST",
            headers=deriv_headers(base_api.CONFIG.deriv.app_id, bearer_token=token),
        )
        try:
            with urlopen(provider_request, timeout=7.0) as response:  # nosec B310 - configured Deriv API origin
                status = int(getattr(response, "status", 200) or 200)
        except HTTPError as exc:
            detail = _provider_error(exc)
            status_code = 400 if exc.code == 400 else 401 if exc.code == 401 else 403 if exc.code == 403 else 404 if exc.code == 404 else 502
            raise HTTPException(status_code=status_code, detail=detail) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=503, detail="Deriv demo balance reset is temporarily unavailable") from exc

        if status < 200 or status >= 300:
            raise HTTPException(status_code=502, detail="Deriv did not confirm the demo balance reset")

        # Deriv's current Options API defines the reset target as USD 10,000. Keep
        # the local dashboard snapshot immediately consistent; the authenticated
        # browser balance subscription will confirm the selected account live.
        try:
            base_api.REPOSITORY.update_account_balance(
                account_id=account_id,
                balance=DEFAULT_DEMO_BALANCE,
                currency="USD",
                status="active",
            )
        except Exception:
            pass
        try:
            base_api.mark_dashboard_dirty("demo")
        except Exception:
            pass

        return {
            "success": True,
            "managed_account_id": target_id,
            "account_id": account_id,
            "account_type": "demo",
            "balance": DEFAULT_DEMO_BALANCE,
            "currency": "USD",
            "provider": "deriv",
            "endpoint": "reset-demo-balance",
            "message": "Demo balance reset to $10,000 USD.",
        }

    app.state.vps_demo_balance_reset_installed = True
    _INSTALLED = True
