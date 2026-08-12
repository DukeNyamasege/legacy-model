from __future__ import annotations

import asyncio
import time
from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app.account_execution_session import (
    AccountExecutionError,
    AccountExecutionSession,
)
from app.rf_dir5_bot import RFDir5TradingBot
from app.strategy.decision_engine import ProposalEconomics, parse_proposal_economics
from enhanced_bot import TradingBot, mask_account_id, sanitize_account_ids


_INSTALLED = False


async def _proposal_on_exact_account_session(
    self: AccountExecutionSession,
    signal: Any,
    *,
    stake: float,
    predicted_probability: float,
) -> ProposalEconomics:
    """Create the proposal on the same authenticated account WS used to buy it.

    Deriv proposal IDs are consumed by the WebSocket trading session that owns the
    proposal. Creating the proposal on the shared public socket and buying it on an
    account private socket can therefore produce `Unknown contract proposal` even
    though both requests are individually valid.
    """

    _state, private_session = self.prepare()
    requested = time.monotonic()
    response = await private_session.send_request(self._proposal_request(signal, stake))
    received = time.monotonic()
    if "error" in response:
        message = sanitize_account_ids(
            str((response.get("error") or {}).get("message") or "Proposal failed")
        )
        raise AccountExecutionError(message)
    try:
        return parse_proposal_economics(
            response,
            stake=round(float(stake), 2),
            predicted_probability=float(predicted_probability),
            requested_monotonic=requested,
            received_monotonic=received,
            app_markup_percentage=float(
                getattr(self.bot, "app_markup_percentage", 0.0) or 0.0
            ),
        )
    except ValueError as exc:
        raise AccountExecutionError(str(exc)) from exc


def _active_account_unresolved_rows(bot: RFDir5TradingBot, rows: list[Any]) -> list[Any]:
    """Return unresolved contracts owned by currently active account sessions only."""

    active_masks = {
        mask_account_id(str(account_id))
        for _token, account_id in list(getattr(bot, "valid_clients", []) or [])
        if str(account_id or "").strip()
    }
    if not active_masks:
        return []
    return [
        row
        for row in rows
        if str(getattr(row, "account_id_masked", "") or "").strip() in active_masks
    ]


def _install_account_scoped_unresolved_filter(bot: RFDir5TradingBot) -> None:
    repository = bot.repository
    original = repository.unresolved_contracts

    def account_scoped_unresolved_contracts() -> list[Any]:
        rows = list(original())
        relevant = _active_account_unresolved_rows(bot, rows)
        ignored = len(rows) - len(relevant)
        if ignored:
            bot.logger.debug(
                "CUSTOM_HISTORICAL_UNRESOLVED_IGNORED count=%s reason=not_active_account",
                ignored,
            )
        return relevant

    repository.unresolved_contracts = account_scoped_unresolved_contracts  # type: ignore[method-assign]


def install_custom_strategy_current_runtime_fix() -> None:
    """Final current-runtime fixes for independent per-account Custom Strategies."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Proposal and buy must use the exact same authenticated account WebSocket.
    AccountExecutionSession.proposal = _proposal_on_exact_account_session  # type: ignore[method-assign]

    original_fail_closed = direct_runtime._fail_closed

    def fail_closed_immediately(
        bot: RFDir5TradingBot,
        managed_id: int,
        reason: str,
        *,
        log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
    ) -> None:
        original_fail_closed(
            bot,
            managed_id,
            reason,
            log_event=log_event,
        )

        # Remove the account from the hot-path registry immediately. Previously
        # the failed account stayed in _custom_direct_accounts until the next
        # periodic refresh, allowing several more qualifying ticks to schedule
        # duplicate failing purchase attempts after the first fatal error.
        runtime = getattr(bot, "_custom_direct_accounts", {})
        runtime.pop(int(managed_id), None)
        getattr(bot, "_custom_direct_virtual_due", {}).pop(int(managed_id), None)
        getattr(bot, "_custom_direct_inflight", set()).discard(int(managed_id))

        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        for task in list(getattr(bot, "_custom_direct_tasks", set()) or set()):
            if task is current or task.done():
                continue
            if task.get_name().startswith(f"custom_direct_{int(managed_id)}_"):
                task.cancel()

    direct_runtime._fail_closed = fail_closed_immediately

    # The inherited RFDir5 private-ready callback starts RF contract validation.
    # Custom Strategy execution does not use RF contract routing; call the base
    # TradingBot account-ready hook directly, then register the direct runtime.
    def private_ready_without_rf_validation(
        self: RFDir5TradingBot,
        session: Any,
    ) -> None:
        TradingBot._on_private_session_ready(self, session)
        managed_id = getattr(session, "managed_account_id", None)
        if managed_id is None:
            return
        try:
            self._sync_clients_with_runtime_accounts()
            runtime = direct_runtime._refresh_direct_accounts(
                self,
                require_connected=True,
                fail_invalid=False,
            )
            if int(managed_id) in runtime:
                self._set_account_execution_status(
                    int(managed_id),
                    "waiting_for_condition",
                    "Authenticated account execution session is ready",
                )
        except Exception:
            self.logger.exception(
                "CUSTOM_PRIVATE_READY_VALIDATION_FAILED managed_id=%s",
                managed_id,
            )

    RFDir5TradingBot._on_private_session_ready = private_ready_without_rf_validation

    # Historical unresolved contracts for other accounts are audit data, not an
    # execution error for the account that a trader has just started. Keep crash
    # recovery for the active account only and leave unrelated rows untouched.
    original_init = RFDir5TradingBot.__init__

    def current_runtime_init(
        self: RFDir5TradingBot,
        config_path: str | None = None,
    ) -> None:
        original_init(self, config_path)
        _install_account_scoped_unresolved_filter(self)
        self.logger.warning(
            "CUSTOM_CURRENT_RUNTIME_FIX_ACTIVE proposal_transport=account_private_websocket "
            "rf_contract_validation=false unresolved_scope=active_account fail_closed=immediate"
        )

    RFDir5TradingBot.__init__ = current_runtime_init
    RFDir5TradingBot._custom_current_runtime_fix_installed = True
    _INSTALLED = True
