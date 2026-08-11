from __future__ import annotations

from typing import Any

from app.oauth_client import refresh_access_token, token_is_expiring
from app.token_store import decrypt_auth_payload, encrypt_auth_payload
from enhanced_bot import TradingBot, normalize_account_type, mask_account_id
from app.account_mode_execution_lock import (
    account_allows_new_execution,
    account_lifecycle_from_row,
)

_INSTALLED = False
_ORIGINAL_LOAD_RUNTIME_ACCOUNTS = None


def _refresh_enabled_oauth_rows(bot: TradingBot) -> None:
    """Refresh each enabled row's own OAuth credential before runtime loading."""

    for row in bot.repository.list_managed_accounts():
        lifecycle = account_lifecycle_from_row(row)
        if not account_allows_new_execution(row) and lifecycle != "settlement":
            continue
        try:
            payload = decrypt_auth_payload(row.token_secret, bot.encryption_key)
        except Exception:
            continue
        if str(payload.get("auth_type") or "").strip().lower() != "oauth":
            continue
        if not token_is_expiring(payload):
            continue
        refresh_token_value = str(payload.get("refresh_token") or "").strip()
        if not refresh_token_value:
            bot._set_account_execution_status(
                int(row.id),
                "token_required",
                "OAuth session cannot be refreshed. Log in again with trade permission.",
            )
            continue
        try:
            refreshed = refresh_access_token(
                client_id=str(bot.test2_config.deriv.oauth_client_id or bot.app_id),
                refresh_token=refresh_token_value,
            )
            payload.update(refreshed)
            bot.repository.update_managed_account(
                int(row.id),
                token_secret=encrypt_auth_payload(payload, bot.encryption_key),
                enabled=True,
            )
            bot.logger.info(
                "ACCOUNT_OAUTH_REFRESHED managed_id=%s account=%s",
                int(row.id),
                mask_account_id(str(payload.get("account_id") or "")),
            )
        except Exception as exc:
            bot._set_account_execution_status(
                int(row.id),
                "reconnecting",
                "OAuth refresh failed temporarily; this account will retry independently.",
            )
            bot.logger.warning(
                "ACCOUNT_OAUTH_REFRESH_FAILED managed_id=%s error=%s",
                int(row.id),
                type(exc).__name__,
            )


def _account_scoped_runtime_accounts(self: TradingBot):
    """Remove sibling-token borrowing from the final private WS account set.

    The base loader historically supported shared credentials and could borrow a
    PAT from another row belonging to the same login. Production now uses one
    authenticated WebSocket per account. Each runtime profile therefore keeps
    only the credential stored on that exact ManagedAccount row. OAuth access
    tokens may be identical across rows from one Deriv login, but each row still
    carries and validates its own account_id/account_type pairing.
    """

    original = _ORIGINAL_LOAD_RUNTIME_ACCOUNTS
    if original is None:
        raise RuntimeError("Original runtime account loader is unavailable")

    _refresh_enabled_oauth_rows(self)
    runtime_keys, profiles = original(self)
    filtered_keys: list[str] = []
    filtered_profiles: dict[str, dict[str, Any]] = {}
    seen_accounts: set[str] = set()

    for runtime_key in runtime_keys:
        profile = dict(profiles.get(runtime_key) or {})
        managed_id = profile.get("managed_account_id")
        try:
            managed_id_int = int(managed_id)
        except (TypeError, ValueError):
            continue
        row = self.repository.managed_account(managed_id_int)
        lifecycle = account_lifecycle_from_row(row) if row else "missing"
        if not row or (not account_allows_new_execution(row) and lifecycle != "settlement"):
            continue
        try:
            payload = decrypt_auth_payload(row["token_secret"], self.encryption_key)
        except Exception:
            self._set_account_execution_status(
                managed_id_int,
                "credential_error",
                "Stored Deriv credential could not be read for this account.",
            )
            continue

        account_id = str(payload.get("account_id") or "").strip()
        account_type = normalize_account_type(
            payload.get("account_type") or payload.get("environment"),
            self.environment,
        )
        own_credential = str(self._purchase_token_from_payload(payload) or "").strip()
        if not account_id or not own_credential:
            self._set_account_execution_status(
                managed_id_int,
                "token_required",
                (
                    f"{account_type.upper()} account needs an OAuth access token or PAT "
                    "with trade permission for authenticated WebSocket execution."
                ),
            )
            self.logger.warning(
                "ACCOUNT_SCOPED_CREDENTIAL_MISSING managed_id=%s mode=%s account=%s",
                managed_id_int,
                account_type,
                mask_account_id(account_id),
            )
            continue
        if account_id in seen_accounts:
            self._set_account_execution_status(
                managed_id_int,
                "duplicate",
                "This Deriv account is already represented by another active row.",
            )
            continue

        seen_accounts.add(account_id)
        profile.update(
            {
                "api_token": own_credential,
                "account_id": account_id,
                "account_type": account_type,
                "auth_type": str(payload.get("auth_type") or "oauth").strip().lower(),
                "source": "private_websocket_account_scoped",
                "managed_account_id": managed_id_int,
            }
        )
        filtered_keys.append(runtime_key)
        filtered_profiles[runtime_key] = profile
        self.logger.info(
            "ACCOUNT_SCOPED_WEBSOCKET_RUNTIME managed_id=%s account=%s mode=%s "
            "credential=%s",
            managed_id_int,
            mask_account_id(account_id),
            account_type,
            "oauth" if profile["auth_type"] == "oauth" else "pat",
        )

    return filtered_keys, filtered_profiles


def install_account_scoped_websocket_runtime() -> None:
    global _INSTALLED, _ORIGINAL_LOAD_RUNTIME_ACCOUNTS
    if _INSTALLED:
        return
    _ORIGINAL_LOAD_RUNTIME_ACCOUNTS = TradingBot._load_runtime_accounts
    TradingBot._load_runtime_accounts = _account_scoped_runtime_accounts
    TradingBot._account_scoped_websocket_runtime_installed = True
    _INSTALLED = True
