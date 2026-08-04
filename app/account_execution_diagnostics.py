from __future__ import annotations

from typing import Any

from app.rf_dir5_bot import RFDir5TradingBot
from enhanced_bot import TradingBot, mask_account_id, sanitize_account_ids


_TRANSIENT_STATUSES = {
    "settling",
    "reconnecting",
    "contract_verification_pending",
    "execution_waiting",
    "signal_waiting",
    "cycle_skipped",
}


def _token_for_account(bot: TradingBot, account_id: str) -> str | None:
    target = str(account_id or "")
    for token, candidate in list(getattr(bot, "valid_clients", []) or []):
        if str(candidate) == target:
            return token
    return None


def install_account_execution_diagnostics() -> None:
    if getattr(RFDir5TradingBot, "_account_execution_diagnostics_installed", False):
        return

    original_eligible_accounts = RFDir5TradingBot._eligible_purchase_accounts
    original_contract_support = getattr(TradingBot, "_account_supports_contract", None)
    original_register_purchase = TradingBot._register_account_purchase

    def eligible_accounts_with_feedback(self: RFDir5TradingBot) -> list[tuple[str, str]]:
        eligible = original_eligible_accounts(self)
        eligible_tokens = {token for token, _account_id in eligible}

        for token, account_id in list(getattr(self, "valid_clients", []) or []):
            managed_id = self._managed_account_id_for_token(token)
            if managed_id is None:
                continue
            session = self.sessions.get(token)

            if token in eligible_tokens:
                account = self.repository.managed_account(managed_id) or {}
                status = str(account.get("execution_status") or "").lower()
                if status in _TRANSIENT_STATUSES and session is not None and session.is_connected:
                    self._set_account_execution_status(
                        managed_id,
                        "active",
                        "Private trading connection is active and account is purchase eligible",
                    )
                continue

            if session is not None and session.pending_contracts:
                count = len(session.pending_contracts)
                self._set_account_execution_status(
                    managed_id,
                    "settling",
                    (
                        f"Skipped this system entry because {count} previous contract"
                        f"{' is' if count == 1 else 's are'} still settling. "
                        "The account will rejoin automatically after settlement."
                    ),
                )
            elif session is None or not session.is_connected:
                self._set_account_execution_status(
                    managed_id,
                    "reconnecting",
                    (
                        "Skipped this system entry because the private trading connection "
                        "was not ready. The worker is reconnecting automatically."
                    ),
                )
            else:
                self._set_account_execution_status(
                    managed_id,
                    "execution_waiting",
                    (
                        "This account was not eligible for the current purchase cycle. "
                        "No money was charged; the next qualifying system entry will be retried."
                    ),
                )
        return eligible

    if callable(original_contract_support):
        def contract_support_with_feedback(
            self: TradingBot,
            *args: Any,
            **kwargs: Any,
        ) -> bool:
            supported = bool(original_contract_support(self, *args, **kwargs))
            account_id = str(kwargs.get("account_id") or "")
            symbol = str(kwargs.get("symbol") or "market")
            contract_type = str(kwargs.get("contract_type") or "contract")
            token = _token_for_account(self, account_id)
            managed_id = self._managed_account_id_for_token(token) if token else None
            if managed_id is None:
                return supported

            if not supported:
                self._set_account_execution_status(
                    managed_id,
                    "contract_verification_pending",
                    (
                        f"Skipped this system entry because {contract_type} on {symbol} "
                        "has not yet been verified for this account. The bot will retry automatically."
                    ),
                )
            else:
                account = self.repository.managed_account(managed_id) or {}
                if str(account.get("execution_status") or "").lower() == "contract_verification_pending":
                    self._set_account_execution_status(
                        managed_id,
                        "active",
                        "Contract support verified; account execution is active",
                    )
            return supported

        TradingBot._account_supports_contract = contract_support_with_feedback

    async def register_purchase_with_feedback(
        self: TradingBot,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        token = str(kwargs.get("token") or "")
        account_id = str(kwargs.get("account_id") or "")
        managed_id = self._managed_account_id_for_token(token) if token else None
        try:
            return await original_register_purchase(self, *args, **kwargs)
        except Exception as exc:
            reason = sanitize_account_ids(str(exc))
            if managed_id is not None:
                self._set_account_execution_status(
                    managed_id,
                    "purchase_registration_error",
                    (
                        "A Deriv purchase response could not be registered safely in the local ledger: "
                        f"{reason}. Trading is paused to prevent an unknown duplicate position."
                    ),
                )
                self.valid_clients = [
                    item for item in self.valid_clients if item[0] != token
                ]
            self.logger.error(
                "ACCOUNT_PURCHASE_REGISTRATION_PAUSED account=%s error=%s",
                mask_account_id(account_id),
                reason,
            )
            raise

    RFDir5TradingBot._eligible_purchase_accounts = eligible_accounts_with_feedback
    TradingBot._register_account_purchase = register_purchase_with_feedback
    RFDir5TradingBot._account_execution_diagnostics_installed = True
