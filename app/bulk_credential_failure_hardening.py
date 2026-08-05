from __future__ import annotations

from typing import Any

from app.token_store import decrypt_auth_payload
from enhanced_bot import TradingBot, sanitize_account_ids


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


def is_token_account_binding_failure(status: str, reason: str) -> bool:
    """Identify a permanent token/account mismatch without rejecting shared PATs.

    Deriv returns ``BadInputRequest`` for this case instead of ``InvalidToken``.
    Treating it as a generic transient error makes the same account retry every
    qualifying cycle. Treating it as ``credential_error`` is also unsafe because
    that legacy status removes the PAT from every sibling account that shares it.
    The correct action is therefore to isolate only the requested managed account.
    """

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"error", "credential_error", "reconnecting"}:
        return False
    normalized_reason = " ".join(str(reason or "").lower().split())
    return any(marker in normalized_reason for marker in _BINDING_FAILURE_MARKERS)


def quarantine_undecryptable_enabled_accounts(bot: Any) -> int:
    """Disable only enabled rows that cannot be opened with the active key.

    Disabled historical rows are preserved as-is. No token, encrypted payload or
    exception text is logged. A user can repair the isolated account by reconnecting
    it, which writes a fresh encrypted credential under the current worker key.
    """

    quarantined = 0
    encryption_key = str(getattr(bot, "encryption_key", "") or "").strip()
    if not encryption_key:
        return 0

    for row in list(bot.repository.list_managed_accounts()):
        if not bool(getattr(row, "enabled", False)):
            continue
        try:
            payload = decrypt_auth_payload(row.token_secret, encryption_key)
            if not isinstance(payload, dict):
                raise ValueError("credential payload is not an object")
        except Exception:
            bot.repository.quarantine_managed_account(
                int(row.id),
                "credential_decrypt_error",
                _DECRYPT_FAILURE_REASON,
            )
            quarantined += 1
            bot.logger.warning(
                "ACCOUNT_CREDENTIAL_DECRYPT_QUARANTINED managed_id=%s "
                "credential_preserved=true global_execution_continues=true",
                int(row.id),
            )

    return quarantined


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
        current_status(self, managed_account_id, status, sanitize_account_ids(reason))

    def load_accounts_after_credential_quarantine(self: TradingBot):
        quarantined = quarantine_undecryptable_enabled_accounts(self)
        if quarantined:
            self.logger.warning(
                "UNDECRYPTABLE_ACCOUNT_QUARANTINE_COMPLETE accounts=%s "
                "credentials_preserved=true global_execution_continues=true",
                quarantined,
            )
        return current_load(self)

    account_local_execution_status._bulk_credential_failure_hardening = True
    load_accounts_after_credential_quarantine._bulk_credential_failure_hardening = True
    TradingBot._set_account_execution_status = account_local_execution_status
    TradingBot._load_runtime_accounts = load_accounts_after_credential_quarantine
    TradingBot._bulk_credential_failure_hardening_installed = True
    _INSTALLED = True
