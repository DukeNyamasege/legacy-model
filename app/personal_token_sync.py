from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import HTTPException, Request
from fastapi.responses import Response

import app.api as base_api
from app.token_store import decrypt_auth_payload, encrypt_auth_payload


_INSTALLED = False
_VERSION = "20260805-linked-account-token-sync-1"
_TOKEN_REQUIRED_STATUSES = {
    "credential_error",
    "credential_decrypt_error",
    "token_required",
    "bulk_execution_pat_required",
    "invalid_account",
}
_TOKEN_REJECTION_MARKERS = (
    "expired",
    "invalid",
    "rejected",
    "token or account validation failed",
    "token does not belong",
    "credential does not belong",
    "account validation failed",
    "cannot be decrypted",
)


_TOKEN_SYNC_JS = r'''
/* FOA_LINKED_ACCOUNT_TOKEN_SYNC: one trade-scoped token, exact account validation. */
(() => {
  "use strict";
  const VERSION = "20260805-1";

  function applyCopy() {
    const card = document.querySelector(".foa-token-card");
    if (!card) return;

    const description = card.querySelector(".foa-card-head p");
    if (description) {
      description.textContent = "One trade-scoped token can authorize linked Demo and Real Options accounts.";
    }

    const label = card.querySelector("#token-form label > span");
    if (label) label.textContent = "Deriv trade-scoped API token";

    const input = card.querySelector("#token-form input[name='api_token']");
    if (input) {
      input.placeholder = "Paste one token for your linked Options accounts";
      input.setAttribute("aria-describedby", "foa-token-sync-guidance");
    }

    const note = card.querySelector(".foa-security-note p");
    if (note) {
      note.id = "foa-token-sync-guidance";
      note.textContent = "The token is verified against the selected account ID, its trade permission and active Options status. When valid, it is encrypted and synchronized to every Demo or Real account returned by Deriv for that token. Invalid or unrelated tokens are rejected.";
    }

    card.dataset.linkedAccountTokenSync = VERSION;
    document.body.dataset.foaLinkedAccountTokenSync = VERSION;
  }

  const observer = new MutationObserver(applyCopy);
  document.addEventListener("DOMContentLoaded", () => {
    applyCopy();
    observer.observe(document.body, { childList: true, subtree: true });
  }, { once: true });
  if (document.readyState !== "loading") {
    applyCopy();
    observer.observe(document.body, { childList: true, subtree: true });
  }
  window.FOA_LINKED_ACCOUNT_TOKEN_SYNC = VERSION;
})();
'''


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


def _requires_api_token(status: object) -> bool:
    return str(status or "").strip().lower() in _TOKEN_REQUIRED_STATUSES


def _token_was_rejected(status: object, reason: object = "") -> bool:
    normalized = str(status or "").strip().lower()
    message = " ".join(str(reason or "").strip().lower().split())
    if normalized in {"credential_error", "credential_decrypt_error", "invalid_account"}:
        return True
    return normalized in _TOKEN_REQUIRED_STATUSES and any(
        marker in message for marker in _TOKEN_REJECTION_MARKERS
    )


def _provider_account_type(account: dict[str, Any]) -> str:
    raw = str(account.get("account_type") or "").strip().lower()
    if raw not in {"demo", "real"}:
        raise HTTPException(
            status_code=400,
            detail="Deriv returned an unsupported Options account type.",
        )
    return raw


def _provider_account_status(account: dict[str, Any]) -> str:
    return str(account.get("status") or "active").strip().lower() or "active"


def _provider_accounts_by_id(accounts: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(accounts, list):
        raise HTTPException(
            status_code=400,
            detail="Deriv account verification returned an invalid account list.",
        )
    result: dict[str, dict[str, Any]] = {}
    for item in accounts:
        if not isinstance(item, dict):
            continue
        account_id = str(item.get("account_id") or "").strip()
        if not account_id:
            continue
        _provider_account_type(item)
        result[account_id] = item
    return result


def _safe_provider_error(exc: requests.HTTPError) -> tuple[int, str]:
    response = exc.response
    status_code = int(response.status_code) if response is not None else 400
    detail = ""
    if response is not None:
        try:
            payload = response.json()
            errors = payload.get("errors") if isinstance(payload, dict) else None
            if isinstance(errors, list) and errors:
                first = errors[0] if isinstance(errors[0], dict) else {}
                detail = str(first.get("message") or first.get("code") or "").strip()
        except Exception:
            detail = str(response.text or "").strip()
    detail = " ".join(detail.split())[:360]
    if status_code in {401, 403}:
        return 400, detail or "The token is invalid, expired, or missing trade permission."
    return 400, detail or "Deriv rejected the token verification request."


def _reject_current_account(account: dict[str, Any], reason: str) -> None:
    base_api.REPOSITORY.quarantine_managed_account(
        int(account["id"]),
        "token_required",
        str(reason or "A valid trade-scoped Deriv API token is required.")[:160],
    )


def _load_managed_payload(row: Any, current_account: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = decrypt_auth_payload(
            row.token_secret,
            base_api.CONFIG.deriv.token_encryption_key,
        )
        return payload if isinstance(payload, dict) else {}
    except Exception:
        if int(row.id) != int(current_account["id"]):
            return {}
        return {
            "auth_type": "oauth",
            "account_id": str(current_account["account_id"]).strip(),
            "account_type": str(current_account.get("account_type") or "demo").strip().lower(),
        }


def _synced_payload(
    payload: dict[str, Any],
    *,
    api_token: str,
    provider_account: dict[str, Any],
    verified_at: str,
    verified_account_ids: list[str],
) -> dict[str, Any]:
    account_id = str(provider_account.get("account_id") or "").strip()
    account_type = _provider_account_type(provider_account)
    updated = base_api.attach_pat_to_payload(
        payload,
        api_token=api_token,
        account_id=account_id,
        account_type=account_type,
        verified_at=verified_at,
    )
    updated.update(
        {
            "pat_token": api_token,
            "pat_verified_scope": "trade",
            "pat_verified_account_ids": list(verified_account_ids),
            "pat_verified_account_type": account_type,
            "pat_verified_account_status": _provider_account_status(provider_account),
            "pat_shared_demo_real": True,
        }
    )
    return updated


def _install_token_route(app: Any) -> None:
    _remove_route(app, "/me/api-token", "POST")

    PersonalApiTokenRequest = base_api.PersonalApiTokenRequest

    @app.post("/me/api-token")
    def save_and_sync_personal_api_token(
        request: Request,
        body: PersonalApiTokenRequest,
    ) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")

        api_token = str(body.api_token or "").strip()
        if not api_token:
            raise HTTPException(status_code=400, detail="Enter a Deriv API token.")
        if not base_api.has_encryption_key(base_api.CONFIG.deriv.token_encryption_key):
            raise HTTPException(
                status_code=409,
                detail="DERIV_TOKEN_ENCRYPTION_KEY is required before storing API tokens.",
            )

        try:
            provider_accounts = base_api.load_options_accounts(api_token)
        except requests.HTTPError as exc:
            status_code, reason = _safe_provider_error(exc)
            _reject_current_account(account, reason)
            base_api.REPOSITORY.audit(
                "PERSONAL_API_TOKEN_REJECTED",
                "account-dashboard",
                request.client.host if request.client else "unknown",
                {
                    "account_id_masked": account.get("account_id_masked", ""),
                    "reason": reason,
                },
            )
            raise HTTPException(status_code=status_code, detail=f"API token rejected: {reason}")
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Deriv token verification is temporarily unavailable. "
                    f"No credential was changed: {type(exc).__name__}"
                ),
            )

        accounts_by_id = _provider_accounts_by_id(provider_accounts)
        current_account_id = str(account["account_id"]).strip()
        current_provider_account = accounts_by_id.get(current_account_id)
        if current_provider_account is None:
            reason = (
                "This token is valid, but Deriv did not return the selected Options "
                "account. Use a token belonging to this account."
            )
            _reject_current_account(account, reason)
            raise HTTPException(status_code=400, detail=reason)

        provider_status = _provider_account_status(current_provider_account)
        if provider_status not in {"active", "open"}:
            reason = (
                f"The selected Options account is not active in Deriv "
                f"(status: {provider_status})."
            )
            _reject_current_account(account, reason)
            raise HTTPException(status_code=400, detail=reason)

        verified_at = datetime.now(timezone.utc).isoformat()
        verified_ids = sorted(accounts_by_id)
        synced_types: set[str] = set()
        synced_accounts: list[str] = []

        for managed_row in base_api.REPOSITORY.list_managed_accounts():
            payload = _load_managed_payload(managed_row, account)
            managed_account_id = str(payload.get("account_id") or "").strip()
            provider_account = accounts_by_id.get(managed_account_id)
            if provider_account is None:
                continue
            provider_type = _provider_account_type(provider_account)
            updated_payload = _synced_payload(
                payload,
                api_token=api_token,
                provider_account=provider_account,
                verified_at=verified_at,
                verified_account_ids=verified_ids,
            )
            label_prefix = "Demo" if provider_type == "demo" else "Real"
            label = (
                f"{label_prefix} {managed_account_id[:3]}***{managed_account_id[-3:]}"
                if len(managed_account_id) > 6
                else f"{label_prefix} Account"
            )
            base_api.REPOSITORY.update_managed_account(
                int(managed_row.id),
                label=label,
                token_secret=encrypt_auth_payload(
                    updated_payload,
                    base_api.CONFIG.deriv.token_encryption_key,
                ),
                enabled=bool(managed_row.enabled),
            )
            base_api.REPOSITORY.set_managed_account_execution_status(
                int(managed_row.id),
                "connecting" if bool(managed_row.enabled) else "disabled",
                (
                    "Trade-scoped token verified; account connection is starting"
                    if bool(managed_row.enabled)
                    else "Trade-scoped token verified; start AutoTrade when ready"
                ),
            )
            synced_types.add(provider_type)
            synced_accounts.append(base_api.mask_account_id(managed_account_id))
            try:
                base_api.REPOSITORY.update_account_balance(
                    account_id=managed_account_id,
                    balance=float(provider_account.get("balance", 0.0)),
                    currency=str(provider_account.get("currency", "USD")),
                    status=str(provider_account.get("status", "active")),
                )
            except (TypeError, ValueError):
                pass

        if base_api.mask_account_id(current_account_id) not in synced_accounts:
            reason = "The selected account could not be synchronized after verification."
            _reject_current_account(account, reason)
            raise HTTPException(status_code=409, detail=reason)

        base_api.REPOSITORY.audit(
            "PERSONAL_API_TOKEN_VERIFIED_AND_SYNCED",
            "account-dashboard",
            request.client.host if request.client else "unknown",
            {
                "account_id_masked": base_api.mask_account_id(current_account_id),
                "provider_account_type": _provider_account_type(current_provider_account),
                "shared_account_types": sorted(synced_types),
                "synced_account_count": len(synced_accounts),
                "trade_scope_verified": True,
            },
        )
        return {
            "success": True,
            "has_trading_api_token": True,
            "requires_api_token": False,
            "account_id": base_api.mask_account_id(current_account_id),
            "provider_account_type": _provider_account_type(current_provider_account),
            "shared_account_types": sorted(synced_types),
            "synced_account_count": len(synced_accounts),
            "message": (
                "Token verified and synchronized to every linked Options account "
                "returned by Deriv."
            ),
        }


def _install_final_dashboard_scripts(app: Any) -> None:
    from app.strategy_v2_final_ui import _headers, _script

    for path in ("/ui/dashboard-v2.js", "/ui/simplified-dashboard.js"):
        _remove_route(app, path, "GET")

    def response() -> Response:
        headers = dict(_headers())
        headers.update(
            {
                "X-FOA-Linked-Account-Token-Sync": "1",
                "X-FOA-Token-Sync-Version": _VERSION,
            }
        )
        return Response(
            _script() + "\n" + _TOKEN_SYNC_JS,
            media_type="application/javascript",
            headers=headers,
        )

    app.get("/ui/dashboard-v2.js", include_in_schema=False)(response)
    app.get("/ui/simplified-dashboard.js", include_in_schema=False)(response)


def install_personal_token_sync(app: Any) -> None:
    """Validate one PAT by account ownership and share it across Demo/Real rows."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Existing `/me`, lifecycle and settings code resolves these globals at request
    # time. Extending the blocked statuses makes the token input field reappear for
    # unreadable, rejected and account-mismatch credentials.
    base_api.execution_requires_new_token = _requires_api_token
    base_api.execution_token_was_rejected = _token_was_rejected
    _install_token_route(app)

    @app.on_event("startup")
    async def finalize_linked_account_token_sync() -> None:
        # Final UI routes are installed later during app.api_v3 import. Replacing
        # them at startup preserves every existing UI layer and appends only the
        # linked-account token guidance.
        _install_token_route(app)
        _install_final_dashboard_scripts(app)

    app.state.personal_token_sync_installed = True
    app.state.personal_token_sync_version = _VERSION
    _INSTALLED = True
