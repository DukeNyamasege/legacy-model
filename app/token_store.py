from __future__ import annotations

import json
from typing import Iterable

from cryptography.fernet import Fernet


def has_encryption_key(key: str) -> bool:
    return bool(str(key or "").strip())


def encrypt_token(token: str, key: str) -> str:
    value = str(token or "").strip()
    secret = str(key or "").strip()
    if not value:
        raise ValueError("Token cannot be empty")
    if not secret:
        raise ValueError("DERIV_TOKEN_ENCRYPTION_KEY is required for dashboard token storage")
    return Fernet(secret.encode("utf-8")).encrypt(value.encode("utf-8")).decode("utf-8")


def encrypt_auth_payload(payload: dict, key: str) -> str:
    secret = str(key or "").strip()
    if not secret:
        raise ValueError("DERIV_TOKEN_ENCRYPTION_KEY is required for dashboard token storage")
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return Fernet(secret.encode("utf-8")).encrypt(serialized.encode("utf-8")).decode("utf-8")


def decrypt_token(token_secret: str, key: str) -> str:
    secret = str(key or "").strip()
    if not secret:
        raise ValueError("DERIV_TOKEN_ENCRYPTION_KEY is required for dashboard token storage")
    return Fernet(secret.encode("utf-8")).decrypt(
        str(token_secret).encode("utf-8")
    ).decode("utf-8")


def decrypt_auth_payload(token_secret: str, key: str) -> dict:
    decrypted = decrypt_token(token_secret, key)
    try:
        payload = json.loads(decrypted)
    except json.JSONDecodeError:
        return {
            "auth_type": "pat",
            "access_token": decrypted,
        }
    if not isinstance(payload, dict):
        raise ValueError("Stored token payload must be a JSON object")
    if "access_token" not in payload:
        raise ValueError("Stored token payload is missing access_token")
    payload.setdefault("auth_type", "oauth")
    return payload


def remove_trading_api_token(payload: dict) -> dict:
    """Remove a rejected PAT while retaining OAuth identity/account metadata."""
    updated = dict(payload)
    oauth_access_token = str(updated.pop("oauth_access_token", "") or "").strip()
    oauth_refresh_token = str(updated.pop("oauth_refresh_token", "") or "").strip()
    oauth_expires_at = str(updated.pop("oauth_expires_at", "") or "").strip()
    oauth_scope = str(updated.pop("oauth_scope", "") or "").strip()
    updated.pop("pat_token", None)
    updated.pop("pat_verified_at", None)
    updated["pat_token_set"] = False
    if oauth_access_token or oauth_refresh_token:
        updated.update(
            {
                "auth_type": "oauth",
                "access_token": oauth_access_token,
                "refresh_token": oauth_refresh_token,
                "expires_at": oauth_expires_at,
                "scope": oauth_scope,
                "auth_source": "deriv_oauth",
            }
        )
    else:
        # Keep a valid encrypted payload and account identity, but no credential
        # that can accidentally be reused for trading.
        updated.update(
            {
                "auth_type": "pat",
                "access_token": "",
                "auth_source": "pat_required",
            }
        )
    return updated


def parse_token_lines(raw_text: str) -> list[str]:
    return [
        line.strip()
        for line in str(raw_text or "").splitlines()
        if line.strip()
    ]


def mask_token(token: str) -> str:
    value = str(token or "").strip()
    if len(value) <= 10:
        return "***"
    return f"{value[:6]}...{value[-4:]}"
