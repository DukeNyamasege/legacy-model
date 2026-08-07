from __future__ import annotations

from typing import Any

from app.token_store import decrypt_auth_payload
from enhanced_bot import (
    TradingBot,
    is_permanent_credential_error,
    private_websocket_credential_from_payload,
    sanitize_account_ids,
)


_INSTALLED = False
_BINDING_FAILURE_MARKERS = (
    "token or account validation failed",
    "token does not belong to",
    "credential does not belong to",
    "account validation failed",
)
_DECRYPT_FAILURE_REASON = (
    "The stored credential cannot be decrypted with the current worker key. "
    "Reconnect this account in Settings > Credentials. The encrypted value was preserved."
)
_ACCOUNT_BINDING_REASON = (
    "Deriv rejected this API token for the selected account. Reconnect this account "
    "or replace its trade-scoped API token in Settings > Credentials."
)
_PERMANENT_TOKEN_REASON = (
    "The stored Deriv authorization is invalid or expired. Reconnect this account "
    "or replace its trade-scoped API token in Settings > Credentials."
)
_MISSING_TRADE_CREDENTIAL_REASON = (
    "The stored credential has no usable trade authorization. Reconnect this account "
    "in Settings > Credentials and provide a valid trade-scoped authorization."
)


def is_token_account_binding_failure(status: str, reason: str) -> bool:
    """Identify a permanent token/account mismatch without rejecting shared PATs.

    Deriv returns ``BadInputRequest`` for this case instead of ``InvalidToken``.
    Treating it as a generic transient error makes the same account retry every
    qualifying cycle. Treating it as ``credential_error`` is also unsafe because
    that legacy status can remove a PAT from every sibling account that shares it.
    The correct action is therefore to isolate only the requested managed account.
    """

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"error", "credential_error", "reconnecting"}:
        return False
    normalized_reason = " ".join(str(reason or "").lower().split())
    return any(marker in normalized_reason for marker in _BINDING_FAILURE_MARKERS)


def is_permanent_token_failure(status: str, reason: str) -> bool:
    """Recognize persisted InvalidToken/expired-token rows as non-retryable."""

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {
        "error",
        "credential_error",
        "reconnecting",
        "token_required",
    }:
        return False
    evidence = str(reason or "").strip()
    if not evidence:
        return False
    return is_permanent_credential_error(
        {
            "code": evidence,
            "message": evidence,
        }
    )


def _worker_encryption_key(bot: Any) -> str:
    direct = str(getattr(bot, "encryption_key", "") or "").strip()
    if direct:
        return direct
    config = getattr(bot, "test2_config", None)
    deriv = getattr(config, "deriv", None)
    return str(getattr(deriv, "token_encryption_key", "") or "").strip()


def quarantine_undecryptable_enabled_accounts(bot: Any) -> int:
    """Disable unsafe enabled credential rows before runtime account loading.

    This covers credentials that cannot be decrypted, persisted provider token
    rejections, token/account binding failures, and decrypted payloads that no
    longer contain a usable trade authorization. Disabled historical rows are kept
    unchanged. No token, encrypted payload or exception text is logged.
    """

    quarantined: dict[int, tuple[str, str]] = {}
    encryption_key = _worker_encryption_key(bot)
    if not encryption_key:
        setattr(bot, "_credential_failure_quarantined", quarantined)
        bot.logger.warning(
            "ACCOUNT_CREDENTIAL_QUARANTINE_SKIPPED reason=encryption_key_unavailable "
            "global_execution_continues=true"
        )
        return 0

    for row in list(bot.repository.list_managed_accounts()):
        if not bool(getattr(row, "enabled", False)):
            continue

        managed_id = int(row.id)
        status = str(getattr(row, "execution_status", "") or "")
        reason = str(getattr(row, "execution_status_reason", "") or "")
        if is_token_account_binding_failure(status, reason):
            bot.repository.quarantine_managed_account(
                managed_id,
                "invalid_account",
                _ACCOUNT_BINDING_REASON,
            )
            quarantined[managed_id] = ("invalid_account", _ACCOUNT_BINDING_REASON)
            bot.logger.warning(
                "ACCOUNT_BULK_CREDENTIAL_QUARANTINED managed_id=%s "
                "source=persisted_status token_removed=false "
                "shared_credentials_preserved=true global_execution_continues=true",
                managed_id,
            )
            continue

        if is_permanent_token_failure(status, reason):
            bot.repository.quarantine_managed_account(
                managed_id,
                "token_required",
                _PERMANENT_TOKEN_REASON,
            )
            quarantined[managed_id] = ("token_required", _PERMANENT_TOKEN_REASON)
            bot.logger.warning(
                "ACCOUNT_TRADING_CREDENTIAL_QUARANTINED managed_id=%s "
                "source=persisted_invalid_token token_removed=false "
                "shared_credentials_preserved=true global_execution_continues=true",
                managed_id,
            )
            continue

        try:
            payload = decrypt_auth_payload(row.token_secret, encryption_key)
            if not isinstance(payload, dict):
                raise ValueError("credential payload is not an object")
        except Exception:
            bot.repository.quarantine_managed_account(
                managed_id,
                "credential_decrypt_error",
                _DECRYPT_FAILURE_REASON,
            )
            quarantined[managed_id] = (
                "credential_decrypt_error",
                _DECRYPT_FAILURE_REASON,
            )
            bot.logger.warning(
                "ACCOUNT_CREDENTIAL_DECRYPT_QUARANTINED managed_id=%s "
                "credential_preserved=true global_execution_continues=true",
                managed_id,
            )
            continue

        # Successful decryption alone is not enough. Empty legacy payloads and
        # OAuth credentials without trade scope previously remained enabled and
        # later appeared as anonymous InvalidToken rows in the execution audit.
        usable_trade_credential = str(
            private_websocket_credential_from_payload(payload) or ""
        ).strip()
        if not usable_trade_credential:
            bot.repository.quarantine_managed_account(
                managed_id,
                "token_required",
                _MISSING_TRADE_CREDENTIAL_REASON,
            )
            quarantined[managed_id] = (
                "token_required",
                _MISSING_TRADE_CREDENTIAL_REASON,
            )
            bot.logger.warning(
                "ACCOUNT_TRADING_CREDENTIAL_QUARANTINED managed_id=%s "
                "source=missing_trade_authorization token_removed=false "
                "shared_credentials_preserved=true global_execution_continues=true",
                managed_id,
            )

    setattr(bot, "_credential_failure_quarantined", quarantined)
    return len(quarantined)


def _restore_quarantine_reasons(bot: Any) -> None:
    """Restore exact account repair instructions after legacy disabled-row cleanup."""

    quarantined = dict(getattr(bot, "_credential_failure_quarantined", {}) or {})
    for managed_id, value in quarantined.items():
        status, reason = value
        bot.repository.set_managed_account_execution_status(
            int(managed_id),
            str(status),
            str(reason),
        )


def install_bulk_credential_failure_hardening() -> None:
    """Make permanent bulk credential failures account-local and non-repeating."""

    global _INSTALLED
    if _INSTALLED:
        return

    current_status = TradingBot._set_account_execution_status
    current_load = TradingBot._load_runtime_accounts

    def account_local_execution_status(
        self: TradingBot,
        managed_account_id: int | None,
        status: str,
        reason: str = "",
    ) -> None:
        if managed_account_id is not None and is_token_account_binding_failure(
            status,
            reason,
        ):
            self.repository.quarantine_managed_account(
                int(managed_account_id),
                "invalid_account",
                _ACCOUNT_BINDING_REASON,
            )
            self.logger.warning(
                "ACCOUNT_BULK_CREDENTIAL_QUARANTINED managed_id=%s "
                "reason=token_account_binding_rejected token_removed=false "
                "shared_credentials_preserved=true global_execution_continues=true",
                int(managed_account_id),
            )
            return
        if managed_account_id is not None and is_permanent_token_failure(
            status,
            reason,
        ):
            self.repository.quarantine_managed_account(
                int(managed_account_id),
                "token_required",
                _PERMANENT_TOKEN_REASON,
            )
            self.logger.warning(
                "ACCOUNT_TRADING_CREDENTIAL_QUARANTINED managed_id=%s "
                "source=live_invalid_token token_removed=false "
                "shared_credentials_preserved=true global_execution_continues=true",
                int(managed_account_id),
            )
            return
        current_status(self, managed_account_id, status, sanitize_account_ids(reason))

    def load_accounts_after_credential_quarantine(self: TradingBot):
        quarantined = quarantine_undecryptable_enabled_accounts(self)
        result = current_load(self)
        _restore_quarantine_reasons(self)
        if quarantined:
            self.logger.warning(
                "UNSAFE_ACCOUNT_CREDENTIAL_QUARANTINE_COMPLETE accounts=%s "
                "credentials_preserved=true repair_reasons_preserved=true "
                "global_execution_continues=true",
                quarantined,
            )
        return result

    account_local_execution_status._bulk_credential_failure_hardening = True
    load_accounts_after_credential_quarantine._bulk_credential_failure_hardening = True
    TradingBot._set_account_execution_status = account_local_execution_status
    TradingBot._load_runtime_accounts = load_accounts_after_credential_quarantine
    TradingBot._bulk_credential_failure_hardening_installed = True
    _INSTALLED = True
