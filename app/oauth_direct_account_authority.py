from __future__ import annotations

from typing import Any

from app import api as base_api


_INSTALLED = False
_ORIGINAL_HAS_TRADING_API_TOKEN = base_api.has_trading_api_token


def _scope_set(payload: dict[str, Any], *, oauth_prefix: bool = False) -> set[str]:
    if oauth_prefix:
        raw = payload.get("oauth_scope") or payload.get("scope") or payload.get("scopes")
    else:
        raw = payload.get("scope") or payload.get("oauth_scope") or payload.get("scopes")
    return {
        item.strip().lower()
        for item in str(raw or "").replace(",", " ").split()
        if item.strip()
    }


def oauth_trade_access_token(payload: dict[str, Any]) -> str:
    auth_type = str(payload.get("auth_type") or "").strip().lower()
    if auth_type == "oauth":
        token = str(
            payload.get("access_token") or payload.get("oauth_access_token") or ""
        ).strip()
        scopes = _scope_set(payload)
    else:
        token = str(payload.get("oauth_access_token") or "").strip()
        scopes = _scope_set(payload, oauth_prefix=True)
    return token if token and "trade" in scopes else ""


def has_account_execution_credential(payload: dict[str, Any]) -> bool:
    return bool(oauth_trade_access_token(payload) or _ORIGINAL_HAS_TRADING_API_TOKEN(payload))


def has_personal_account_execution_credential(payload: dict[str, Any]) -> bool:
    return has_account_execution_credential(payload) or bool(
        base_api.shared_trading_api_token(payload)
    )


def merge_oauth_payload(
    existing: dict,
    oauth_payload: dict,
    account_id: str,
    account_type: str = "",
) -> dict:
    """Make the fresh OAuth grant authoritative while retaining PAT only as fallback."""

    merged = dict(oauth_payload)
    merged["auth_type"] = "oauth"
    merged["account_id"] = account_id
    merged["account_type"] = base_api.normalize_account_type(account_type)
    merged["auth_source"] = "deriv_oauth"

    legacy_pat = str(base_api.trading_api_token_from_payload(existing) or "").strip()
    if legacy_pat:
        merged["pat_token"] = legacy_pat
        merged["pat_token_set"] = True
        verified_at = str(existing.get("pat_verified_at") or "").strip()
        if verified_at:
            merged["pat_verified_at"] = verified_at
    return merged


def install_oauth_direct_account_authority() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Existing API compatibility helpers may still need a PAT for legacy bulk
    # endpoints, so do not change trading_api_token_from_payload(). Only the
    # Custom Strategy account-readiness predicates become OAuth-aware.
    base_api.merge_oauth_payload = merge_oauth_payload
    base_api.has_trading_api_token = has_account_execution_credential
    base_api.has_personal_trading_api_token = has_personal_account_execution_credential
    base_api.oauth_direct_account_authority_installed = True
    _INSTALLED = True
