from __future__ import annotations

"""Keep linked-account discovery off the Full-VPS dashboard request path.

The final 6F-2 selector correctly scopes accounts by Deriv login identity, but its
GET route enumerates/decrypts every historical ManagedAccount on every browser
refresh. On a long-lived VPS that can exceed the frontend read timeout. This
hotfix returns the selected account in O(1), warms the complete linked list after
the response, and validates an explicit switch using only current + target rows.

DOT/ROT Options account identifiers are intentionally returned in full to the
authenticated account owner. They are account identifiers, not bearer credentials;
trade secrets/tokens remain server-side and are never returned by these routes.
"""

import threading
import time
from typing import Any

from fastapi import BackgroundTasks, HTTPException, Request

import app.api as base_api
from app.final_linked_accounts_6f2 import LinkedAccountSwitchRequest, _account_payload, _linked_rows
from app.token_store import decrypt_auth_payload

_INSTALLED = False
_CACHE_TTL_SECONDS = 120.0
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_LOCK = threading.RLock()


def _remove_route(app: Any, path: str, method: str) -> None:
    expected = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        )
    ]


def _record(managed_id: int) -> dict[str, Any]:
    row = base_api.REPOSITORY.managed_account(int(managed_id))
    if not row:
        raise HTTPException(status_code=404, detail="Managed account was not found")
    return row


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return decrypt_auth_payload(
            row["token_secret"],
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Account credential is unreadable") from exc


def _identity(payload: dict[str, Any]) -> str:
    account_id = str(payload.get("account_id") or "").strip()
    return base_api.login_identity_from_payload(payload) or f"account:{account_id}"


def _selected_payload(account: dict[str, Any], row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    managed_id = int(account["id"])
    account_id = str(payload.get("account_id") or account.get("account_id") or "").strip()
    account_type = base_api.account_type_from_payload(payload)
    summary = base_api.REPOSITORY.account_summary(account_id, managed_account_id=managed_id)
    return {
        "managed_account_id": managed_id,
        "account_id": account_id,
        "account_id_masked": base_api.mask_account_id(account_id),
        "account_type": account_type,
        "label": str(row.get("label") or f"{account_type.title()} {account_id}"),
        "balance": float(summary.get("balance") or 0.0),
        "currency": str(summary.get("currency") or "USD").upper(),
        "status": str(summary.get("status") or "linked"),
        "selected": True,
        "enabled": bool(row.get("enabled")),
        "execution_status": str(row.get("execution_status") or "inactive"),
        "has_trading_api_token": bool(account.get("has_trading_api_token")),
    }


def _cached(identity: str, selected_id: int) -> list[dict[str, Any]] | None:
    now = time.monotonic()
    with _LOCK:
        entry = _CACHE.get(identity)
        if entry is None or now >= float(entry[0]):
            return None
        return [
            {**item, "selected": int(item.get("managed_account_id") or 0) == int(selected_id)}
            for item in entry[1]
        ]


def _discover(identity: str, current_payload: dict[str, Any]) -> None:
    try:
        rows = _linked_rows(current_payload)
        items: list[dict[str, Any]] = []
        for row, payload in rows:
            account_id = str(payload.get("account_id") or "").strip()
            item = {**_account_payload(row, payload, -1), "selected": False}
            item["account_id"] = account_id
            item["account_id_masked"] = base_api.mask_account_id(account_id)
            items.append(item)
        with _LOCK:
            _CACHE[identity] = (time.monotonic() + _CACHE_TTL_SECONDS, items)
    except Exception:
        # Discovery is an enhancement to the immediate selected-account response;
        # it must never make the dashboard unavailable.
        return


def _invalidate(identity: str = "") -> None:
    with _LOCK:
        if identity:
            _CACHE.pop(identity, None)
        else:
            _CACHE.clear()


def install_vps_linked_accounts_latency_hotfix(app: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/me/accounts", "GET")
    _remove_route(app, "/me/switch-account", "POST")

    @app.get("/me/accounts")
    def fast_linked_accounts(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if account.get("local_dev_preview"):
            account_type = base_api.normalize_account_type(account.get("account_type"))
            preview_id = str(account.get("account_id") or account.get("account_id_masked") or "VRTDEV")
            return {
                "authenticated": True,
                "scope": "linked_options_accounts",
                "selected_managed_account_id": int(account.get("id") or 0),
                "accounts": [{
                    "managed_account_id": int(account.get("id") or 0),
                    "account_id": preview_id,
                    "account_id_masked": str(account.get("account_id_masked") or preview_id),
                    "account_type": account_type,
                    "label": str(account.get("label") or "Local Preview"),
                    "balance": 0.0,
                    "currency": "USD",
                    "status": "preview",
                    "selected": True,
                    "enabled": bool(account.get("enabled")),
                    "execution_status": str(account.get("execution_status") or "inactive"),
                    "has_trading_api_token": bool(account.get("has_trading_api_token")),
                }],
                "linked_accounts_loading": False,
            }

        selected_id = int(account["id"])
        row = _record(selected_id)
        current_payload = _payload(row)
        identity = _identity(current_payload)
        linked = _cached(identity, selected_id)
        if linked is None:
            linked = [_selected_payload(account, row, current_payload)]
            background_tasks.add_task(_discover, identity, current_payload)
            loading = True
        else:
            loading = False
        return {
            "authenticated": True,
            "scope": "linked_options_accounts",
            "selected_managed_account_id": selected_id,
            "accounts": linked,
            "linked_accounts_loading": loading,
            "performance_profile": "vps-linked-accounts-stale-while-revalidate-v2-full-id",
        }

    @app.post("/me/switch-account")
    def fast_switch_linked_account(
        request: Request,
        body: LinkedAccountSwitchRequest,
    ) -> dict[str, Any]:
        session_token = str(request.cookies.get(base_api.CLIENT_SESSION_COOKIE, "") or "")
        account = base_api.get_current_account(request)
        if not account or not session_token:
            raise HTTPException(status_code=401, detail="Not authenticated")

        current_row = _record(int(account["id"]))
        current_payload = _payload(current_row)
        current_identity = _identity(current_payload)
        requested_type = (
            base_api.normalize_account_type(body.account_type)
            if body.account_type is not None
            else None
        )

        target_id = int(body.managed_account_id or 0)
        if target_id <= 0 and requested_type is not None:
            cached = _cached(current_identity, int(account["id"])) or []
            match = next(
                (item for item in cached if str(item.get("account_type") or "") == requested_type),
                None,
            )
            if match is None:
                raise HTTPException(status_code=409, detail="Linked accounts are still loading. Try again shortly.")
            target_id = int(match["managed_account_id"])
        if target_id <= 0:
            raise HTTPException(status_code=422, detail="Choose a linked account")

        target_row = _record(target_id)
        target_payload = _payload(target_row)
        if _identity(target_payload) != current_identity:
            raise HTTPException(status_code=404, detail="That account is not linked to this Deriv login")
        target_type = base_api.account_type_from_payload(target_payload)
        if requested_type is not None and target_type != requested_type:
            raise HTTPException(status_code=422, detail="Selected account type does not match the account")

        base_api.REPOSITORY.set_client_session_account(
            base_api.session_hash(session_token),
            target_id,
        )
        _invalidate(current_identity)
        try:
            base_api.mark_dashboard_dirty(target_type)
        except Exception:
            pass
        account_id = str(target_payload.get("account_id") or "").strip()
        return {
            "success": True,
            "managed_account_id": target_id,
            "account_id": account_id,
            "account_id_masked": base_api.mask_account_id(account_id),
            "account_type": target_type,
        }

    app.state.vps_linked_accounts_latency_hotfix_installed = True
    _INSTALLED = True
