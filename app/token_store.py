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
