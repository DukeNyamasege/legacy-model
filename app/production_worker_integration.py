from __future__ import annotations

import asyncio
import os

import aiohttp

from enhanced_bot import TradingBot, sanitize_account_ids


_INSTALLED = False


def install_production_worker_integration() -> None:
    """Make committed settlements visible to the API after balance reconciliation."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_cleanup = TradingBot._finish_contract_transport_cleanup
    original_load_accounts = TradingBot._load_runtime_accounts

    async def notify_dashboard_with_retry(self: TradingBot) -> None:
        url = os.getenv("INTERNAL_DASHBOARD_REFRESH_URL", "").strip()
        api_key = os.getenv("CONTROL_API_KEY", "").strip()
        if not url or not api_key:
            self.logger.error(
                "DASHBOARD_SETTLEMENT_PUSH_DISABLED url_configured=%s api_key_configured=%s",
                bool(url),
                bool(api_key),
            )
            return

        last_error = "unknown"
        for attempt in range(1, 4):
            try:
                timeout = aiohttp.ClientTimeout(total=3.0)
                async with aiohttp.ClientSession(timeout=timeout) as client:
                    async with client.post(
                        url,
                        headers={"X-API-Key": api_key},
                    ) as response:
                        if response.status < 400:
                            if attempt > 1:
                                self.logger.info(
                                    "DASHBOARD_SETTLEMENT_PUSH_RECOVERED attempt=%s",
                                    attempt,
                                )
                            return
                        last_error = f"HTTP {response.status}"
            except Exception as exc:
                last_error = sanitize_account_ids(str(exc))
            if attempt < 3:
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))

        self.logger.warning(
            "DASHBOARD_SETTLEMENT_PUSH_FAILED attempts=3 error=%s",
            last_error,
        )

    async def cleanup_then_publish(
        self: TradingBot,
        token: str,
        contract_id: int,
        *,
        refresh_balance: bool = True,
    ) -> None:
        await original_cleanup(
            self,
            token,
            contract_id,
            refresh_balance=refresh_balance,
        )
        await self._notify_dashboard_settlement()

    def load_accounts_with_current_transport_status(self: TradingBot):
        result = original_load_accounts(self)
        try:
            for account in self.repository.list_managed_accounts():
                if (
                    str(account.execution_status or "").strip().lower()
                    != "bulk_execution_pat_required"
                ):
                    continue
                self.repository.set_managed_account_execution_status(
                    int(account.id),
                    "token_required",
                    (
                        "A valid Deriv trade authorization is required to establish "
                        "this account's trading session."
                    ),
                )
        except Exception as exc:
            self.logger.warning(
                "ACCOUNT_TRANSPORT_STATUS_REPAIR_FAILED error=%s",
                sanitize_account_ids(str(exc)),
            )
        return result

    TradingBot._notify_dashboard_settlement = notify_dashboard_with_retry
    TradingBot._finish_contract_transport_cleanup = cleanup_then_publish
    TradingBot._load_runtime_accounts = load_accounts_with_current_transport_status

    from app.shared_system_strategy_clock import (
        install_final_shared_system_strategy_clock,
    )
    from app.proposal_execution_recovery import (
        install_proposal_execution_recovery,
    )
    from app.seamless_execution_runtime import (
        install_seamless_execution_runtime,
    )
    from app.seamless_execution_final import (
        install_final_seamless_execution_runtime,
    )
    from app.bulk_response_member_reconciliation import (
        install_bulk_response_member_reconciliation,
    )
    from app.bulk_credential_failure_hardening import (
        install_bulk_credential_failure_hardening,
    )
    from app.tick_sequence_persistence_safety import (
        install_tick_sequence_persistence_safety,
    )
    from app.final_multi_strategy_execution import (
        install_final_multi_strategy_execution,
    )

    install_final_shared_system_strategy_clock()
    install_proposal_execution_recovery()
    install_seamless_execution_runtime()
    install_final_seamless_execution_runtime()

    # Provider bulk responses can use lists or account-keyed mappings. Normalize
    # every successful member and explicit member error before missing-account
    # diagnostics run. Unknown outcomes are never retried blindly.
    install_bulk_response_member_reconciliation()

    # Install after every strategy, proposal and transport wrapper. Temporary
    # live-tick sequence objects are converted to plain integers only during
    # persistence, then restored for the immediate purchase boundary.
    install_tick_sequence_persistence_safety()

    # Account/token binding failures emitted by the final REST bulk transport are
    # quarantined per account. Sibling credentials remain available to valid
    # linked demo and real accounts.
    install_bulk_credential_failure_hardening()

    # Final execution authority: the System purchase uses its already-qualified
    # proposal immediately while all manual contract proposals run independently.
    # One manual group can fail without delaying System or cancelling other groups.
    install_final_multi_strategy_execution()

    TradingBot._production_worker_integration_installed = True
    _INSTALLED = True
