from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import app.api as base_api
from app.final_public_controls import _remove_route
from app.token_store import decrypt_auth_payload


_INSTALLED = False


class LinkedAccountSwitchRequest(BaseModel):
    account_type: str | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        pattern="^(demo|real)$",
    )
    managed_account_id: int | None = Field(default=None, ge=1)


def _managed_payload(row: Any) -> dict[str, Any]:
    return decrypt_auth_payload(
        row.token_secret,
        base_api.CONFIG.deriv.token_encryption_key,
    )


def _linked_rows(current_payload: dict[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
    identity = base_api.login_identity_from_payload(current_payload)
    current_account_id = str(current_payload.get("account_id") or "").strip()
    linked: list[tuple[Any, dict[str, Any]]] = []
    for row in base_api.REPOSITORY.list_managed_accounts():
        try:
            payload = _managed_payload(row)
        except Exception:
            continue
        account_id = str(payload.get("account_id") or "").strip()
        if not account_id:
            continue
        if identity:
            if base_api.login_identity_from_payload(payload) != identity:
                continue
        elif account_id != current_account_id:
            continue
        linked.append((row, payload))
    return sorted(linked, key=lambda item: int(item[0].id))


def _account_payload(row: Any, payload: dict[str, Any], selected_id: int) -> dict[str, Any]:
    account_id = str(payload.get("account_id") or "").strip()
    account_type = base_api.account_type_from_payload(payload)
    summary = base_api.REPOSITORY.account_summary(
        account_id,
        managed_account_id=int(row.id),
    )
    return {
        "managed_account_id": int(row.id),
        "account_id_masked": base_api.mask_account_id(account_id),
        "account_type": account_type,
        "label": str(row.label or f"{account_type.title()} {base_api.mask_account_id(account_id)}"),
        "balance": float(summary.get("balance") or 0.0),
        "currency": str(summary.get("currency") or "USD").upper(),
        "status": str(summary.get("status") or "linked"),
        "selected": int(row.id) == int(selected_id),
        "enabled": bool(row.enabled),
        "execution_status": str(row.execution_status or "inactive"),
        "has_trading_api_token": bool(
            base_api.has_personal_trading_api_token(payload)
            and not base_api.execution_requires_new_token(row.execution_status)
        ),
    }


def install_final_linked_accounts_6f2(app: Any) -> None:
    """Expose and switch only accounts belonging to the authenticated Deriv login.

    The selector changes only ClientSession.managed_account_id. It never mutates
    enabled state, strategy configuration, risk state, recovery state or credentials.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/switch-account", "POST")

    @app.get("/me/accounts")
    def linked_personal_accounts(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if account.get("local_dev_preview"):
            account_type = base_api.normalize_account_type(account.get("account_type"))
            return {
                "authenticated": True,
                "scope": "linked_options_accounts",
                "selected_managed_account_id": int(account.get("id") or 0),
                "accounts": [
                    {
                        "managed_account_id": int(account.get("id") or 0),
                        "account_id_masked": str(account.get("account_id_masked") or "VRT***DEV"),
                        "account_type": account_type,
                        "label": str(account.get("label") or "Local Preview"),
                        "balance": 0.0,
                        "currency": "USD",
                        "status": "preview",
                        "selected": True,
                        "enabled": bool(account.get("enabled")),
                        "execution_status": str(account.get("execution_status") or "inactive"),
                        "has_trading_api_token": bool(account.get("has_trading_api_token")),
                    }
                ],
            }

        row = base_api.REPOSITORY.managed_account(int(account["id"]))
        if not row:
            raise HTTPException(status_code=404, detail="Managed account was not found")
        try:
            current_payload = decrypt_auth_payload(
                row["token_secret"],
                base_api.CONFIG.deriv.token_encryption_key,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Current account credential is unreadable") from exc

        rows = _linked_rows(current_payload)
        return {
            "authenticated": True,
            "scope": "linked_options_accounts",
            "selected_managed_account_id": int(account["id"]),
            "accounts": [
                _account_payload(linked_row, payload, int(account["id"]))
                for linked_row, payload in rows
            ],
        }

    # This endpoint can deliberately return either a normal JSON mapping or a
    # JSONResponse carrying the local-preview account cookie. FastAPI must not try
    # to synthesize a Pydantic response model from that union at application import.
    @app.post("/me/switch-account", response_model=None)
    def switch_linked_personal_account(
        request: Request,
        body: LinkedAccountSwitchRequest,
    ) -> dict[str, Any] | JSONResponse:
        session_token = request.cookies.get(base_api.CLIENT_SESSION_COOKIE)
        account = base_api.get_current_account(request)
        requested_type = (
            base_api.normalize_account_type(body.account_type)
            if body.account_type is not None
            else None
        )

        if account and account.get("local_dev_preview") and base_api.local_dev_auth_allowed(request):
            target_type = requested_type or base_api.normalize_account_type(account.get("account_type"))
            response = JSONResponse({"success": True, "account_type": target_type})
            response.set_cookie(
                key=base_api.LOCAL_DEV_ACCOUNT_TYPE_COOKIE,
                value=target_type,
                httponly=False,
                secure=False,
                samesite="lax",
                max_age=86400,
            )
            return response

        if not account or not session_token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        current_row = base_api.REPOSITORY.managed_account(int(account["id"]))
        if not current_row:
            raise HTTPException(status_code=404, detail="Managed account was not found")
        try:
            current_payload = decrypt_auth_payload(
                current_row["token_secret"],
                base_api.CONFIG.deriv.token_encryption_key,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Current account credential is unreadable") from exc

        linked = _linked_rows(current_payload)
        target: tuple[Any, dict[str, Any]] | None = None
        if body.managed_account_id is not None:
            target = next(
                (
                    item
                    for item in linked
                    if int(item[0].id) == int(body.managed_account_id)
                ),
                None,
            )
            if target is None:
                raise HTTPException(status_code=404, detail="That account is not linked to this Deriv login")
            if requested_type is not None and base_api.account_type_from_payload(target[1]) != requested_type:
                raise HTTPException(status_code=422, detail="Selected account type does not match the account")
        else:
            if requested_type is None:
                raise HTTPException(status_code=422, detail="Choose a linked account")
            target = next(
                (
                    item
                    for item in linked
                    if base_api.account_type_from_payload(item[1]) == requested_type
                ),
                None,
            )
            if target is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No linked {requested_type} account was found for this Deriv login",
                )

        target_row, target_payload = target
        target_type = base_api.account_type_from_payload(target_payload)
        base_api.REPOSITORY.set_client_session_account(
            base_api.session_hash(session_token),
            int(target_row.id),
        )
        base_api.REPOSITORY.audit(
            "PERSONAL_LINKED_ACCOUNT_SWITCHED",
            str(account.get("account_id_masked") or "account"),
            request.client.host if request.client else "unknown",
            {
                "from_managed_account_id": int(account["id"]),
                "to_managed_account_id": int(target_row.id),
                "from_type": str(account.get("account_type") or "demo"),
                "to_type": target_type,
                "trading_state_mutated": False,
            },
        )
        return {
            "success": True,
            "managed_account_id": int(target_row.id),
            "account_id_masked": base_api.mask_account_id(str(target_payload.get("account_id") or "")),
            "account_type": target_type,
        }

    app.state.final_linked_accounts_6f2_installed = True
    _INSTALLED = True
