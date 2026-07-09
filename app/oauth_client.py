from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests


AUTH_BASE_URL = "https://auth.deriv.com/oauth2"
DEFAULT_SCOPES = ("trade", "application_read", "account_manage")


def build_pkce_pair() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return code_verifier, code_challenge


def build_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> tuple[str, str]:
    code_verifier, code_challenge = build_pkce_pair()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(DEFAULT_SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTH_BASE_URL}/auth?{query}", code_verifier


def exchange_code_for_tokens(
    *,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    response = requests.post(
        f"{AUTH_BASE_URL}/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return normalize_token_payload(payload)


def refresh_access_token(*, client_id: str, refresh_token: str) -> dict[str, Any]:
    response = requests.post(
        f"{AUTH_BASE_URL}/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return normalize_token_payload(payload, fallback_refresh_token=refresh_token)


def normalize_token_payload(
    payload: dict[str, Any],
    *,
    fallback_refresh_token: str = "",
) -> dict[str, Any]:
    expires_in = int(payload.get("expires_in", 3600) or 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in))
    return {
        "auth_type": "oauth",
        "access_token": str(payload.get("access_token", "")).strip(),
        "refresh_token": str(payload.get("refresh_token") or fallback_refresh_token).strip(),
        "expires_at": expires_at.isoformat(),
        "scope": str(payload.get("scope", "")).strip(),
        "token_type": str(payload.get("token_type", "Bearer")).strip(),
    }


def token_is_expiring(payload: dict[str, Any], *, within_seconds: int = 180) -> bool:
    expires_at = str(payload.get("expires_at", "")).strip()
    if not expires_at:
        return True
    expiry = datetime.fromisoformat(expires_at)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= datetime.now(timezone.utc) + timedelta(seconds=max(0, within_seconds))
