from __future__ import annotations

import logging
from typing import Any

from app.bulk_credential_failure_hardening import (
    quarantine_undecryptable_enabled_accounts,
)
from app.direct_execution_worker_fence import install_direct_execution_worker_fence
from enhanced_bot import TradingBot


_INSTALLED = False
_VERSION = "credential-quarantine-pre-validation-v1"


def install_credential_quarantine_runtime_guard() -> None:
    """Run final browser-ownership and credential guards before validation.

    The direct-execution fence is installed here because production worker setup
    invokes this function after Custom Strategy runtime is installed. That gives
    the fence the final live references to the custom scanner and exact-scope BUY
    path without changing scheduled/server execution semantics.

    The ordinary credential loader guard remains the primary credential protection.
    This boundary also closes a startup ordering/race gap: an enabled legacy row
    that is undecryptable or already marked InvalidToken is disabled before any
    provider account discovery, OTP or financial execution can use it.
    """

    global _INSTALLED
    install_direct_execution_worker_fence()

    current = TradingBot.validate_accounts
    if getattr(current, "_credential_quarantine_runtime_guard", False):
        _INSTALLED = True
        return

    async def validate_accounts_after_final_credential_sweep(
        self: TradingBot,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        quarantined = quarantine_undecryptable_enabled_accounts(self)
        if quarantined:
            # Rebuild in-memory account/profile state after the database exclusion
            # so the just-quarantined rows cannot remain in this validation pass.
            self._load_runtime_accounts()
            self.logger.warning(
                "PRE_VALIDATION_CREDENTIAL_QUARANTINE_COMPLETE accounts=%s "
                "credentials_preserved=true provider_requests_blocked=true "
                "healthy_accounts_continue=true",
                quarantined,
            )
        return await current(self, *args, **kwargs)

    validate_accounts_after_final_credential_sweep._credential_quarantine_runtime_guard = True  # type: ignore[attr-defined]
    TradingBot.validate_accounts = validate_accounts_after_final_credential_sweep
    TradingBot._credential_quarantine_runtime_guard_installed = True
    TradingBot._credential_quarantine_runtime_guard_version = _VERSION
    logging.getLogger(__name__).warning(
        "CREDENTIAL_QUARANTINE_RUNTIME_GUARD_INSTALLED version=%s "
        "boundary=before_provider_validation credentials_preserved=true "
        "direct_browser_worker_fence=true",
        _VERSION,
    )
    _INSTALLED = True
