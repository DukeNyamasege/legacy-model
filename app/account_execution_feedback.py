from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models import AccountRiskState
from app.recovery import ceil_cents
from app.repositories.rf_dir5_repository import (
    RECOVERY_PENDING,
    RFDir5Repository,
    StakePlan,
    VIRTUAL_MODE,
)
from enhanced_bot import TradingBot, mask_account_id, sanitize_account_ids


SMALL_ACCOUNT_MAX_BALANCE = 50.0
PROVIDER_MINIMUM_STAKE = 0.35

_TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "not connected",
    "connection",
    "temporarily unavailable",
    "network",
    "socket",
)
# Contract-parameter rejections from the Deriv API.  These are NOT connection
# drops — treating them as transient causes an infinite reconnect loop where
# the account is never paused and never successfully trades.  We give them a
# dedicated non-pausing status so the account retries on the next signal while
# the dashboard shows a meaningful explanation instead of "RECONNECTING".
_PARAMETER_ERROR_MARKERS = (
    "input validation",
    "validation failed",
    "invalid parameters",
    "bad parameters",
)
_CREDENTIAL_ERROR_MARKERS = (
    "invalid token",
    "invalid api token",
    "token expired",
    "expired token",
    "authorization",
    "authorisation",
    "unauthorized",
    "forbidden",
    "permission",
    "scope",
)
_CONTRACT_ERROR_MARKERS = (
    "contract",
    "market",
    "symbol",
    "underlying",
    "not offered",
    "not available",
    "invalid proposal",
)


def _error_text(error: Any) -> tuple[str, str]:
    payload = error if isinstance(error, dict) else {}
    code = str(payload.get("code") or "PURCHASE_ERROR").strip()
    message = sanitize_account_ids(
        str(payload.get("message") or "Deriv did not complete this purchase")
    ).strip()
    return code, message


def _status_for_purchase_error(error: Any) -> tuple[str, str, bool]:
    code, message = _error_text(error)
    combined = f"{code} {message}".lower().replace("_", " ")
    if any(marker in combined for marker in _CREDENTIAL_ERROR_MARKERS):
        return "credential_error", f"Trading credential rejected: {message}", True
    # Parameter errors can arrive with broad wrapper text that also mentions the
    # connection or purchase channel. Classify them before transient markers so
    # the dashboard does not get stuck showing a misleading RECONNECTING state.
    if any(marker in combined for marker in _PARAMETER_ERROR_MARKERS):
        # Deriv rejected the contract parameters.  This is not a connection drop —
        # the account will retry automatically on the next qualifying signal.
        return (
            "parameter_error",
            f"Purchase skipped: contract parameters were rejected by the provider "
            f"(will retry on next signal): {message}",
            False,
        )
    if any(marker in combined for marker in _TRANSIENT_ERROR_MARKERS):
        return (
            "reconnecting",
            f"Purchase was skipped while the private trading connection recovers: {message}",
            False,
        )
    if any(marker in combined for marker in _CONTRACT_ERROR_MARKERS):
        return (
            "contract_unavailable",
            f"Contract skipped because Deriv did not accept this market/contract: {message}",
            True,
        )
    return "purchase_error", f"Contract purchase was rejected: {message}", True


def install_account_execution_feedback() -> None:
    """Fail loudly at account level and protect small balances from stake escalation.

    The RF-PUT5 signal brain is untouched. This layer only governs whether a
    particular linked account can execute a selected system trade and what the
    user is told when it cannot.
    """
    if getattr(TradingBot, "_account_execution_feedback_installed", False):
        return

    original_validate_accounts = TradingBot.validate_accounts
    original_purchase_accounts = TradingBot._purchase_accounts_by_stake
    original_plan_stake = RFDir5Repository.plan_stake
    original_start_virtual_trade = RFDir5Repository.start_virtual_trade
    original_settle_virtual_trades = RFDir5Repository.settle_due_virtual_trades

    async def validate_accounts_with_personal_stake(self: TradingBot) -> None:
        await original_validate_accounts(self)

        reserve = max(
            0.0,
            float(
                getattr(
                    getattr(self, "risk_config", None),
                    "minimum_balance_reserve",
                    0.50,
                )
                or 0.0
            ),
        )
        rescued: list[tuple[str, str]] = []

        # Legacy validation compares every account with the model's $0.50
        # reference stake. Correct only false insufficient-balance decisions by
        # recalculating eligibility from that user's saved base stake.
        for token in list(getattr(self, "tokens", []) or []):
            profile = (getattr(self, "user_profiles", {}) or {}).get(token, {})
            managed_id = self._managed_account_id_for_token(token)
            account_id = str(profile.get("account_id") or "").strip()
            if managed_id is None or not account_id:
                continue

            account = self.repository.managed_account(managed_id) or {}
            if str(account.get("execution_status") or "").lower() != "insufficient_balance":
                continue

            summary = self.repository.account_summary(
                account_id,
                managed_account_id=managed_id,
            )
            balance = max(0.0, float(summary.get("balance") or 0.0))
            configured_stake = ceil_cents(
                max(
                    PROVIDER_MINIMUM_STAKE,
                    float(
                        profile.get("stake_amount")
                        or account.get("stake_amount")
                        or PROVIDER_MINIMUM_STAKE
                    ),
                )
            )
            personal_requirement = round(configured_stake + reserve, 2)

            if balance + 1e-9 >= personal_requirement:
                self.repository.update_managed_account(managed_id, enabled=True)
                self.repository.set_managed_account_execution_status(
                    managed_id,
                    "connecting",
                    (
                        f"Balance {balance:.2f} supports your {configured_stake:.2f} base stake "
                        f"plus {reserve:.2f} reserve"
                    ),
                )
                rescued.append((token, account_id))
                self.logger.warning(
                    "ACCOUNT_BALANCE_ELIGIBILITY_CORRECTED account=%s balance=%.2f "
                    "personal_stake=%.2f reserve=%.2f model_reference_stake_ignored=true",
                    mask_account_id(account_id),
                    balance,
                    configured_stake,
                    reserve,
                )
            else:
                self.repository.set_managed_account_execution_status(
                    managed_id,
                    "insufficient_balance",
                    (
                        f"Balance {balance:.2f} is below your current requirement "
                        f"{personal_requirement:.2f} ({configured_stake:.2f} base stake + "
                        f"{reserve:.2f} safety reserve)"
                    ),
                )

        if rescued:
            combined = list(getattr(self, "valid_clients", []) or []) + rescued
            deduplicated: list[tuple[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for item in combined:
                key = (str(item[0]), str(item[1]))
                if key in seen:
                    continue
                seen.add(key)
                deduplicated.append(item)
            self.valid_clients = deduplicated
            self._sync_running_status_after_validation()

    def plan_stake_with_small_account_protection(
        self: RFDir5Repository,
        *,
        managed_account_id: int,
        account_id_masked: str = "",
        current_balance: float,
        requested_stake: float,
        proposal_profit_ratio: float,
        recovery_enabled: bool,
        recovery_trigger_losses: int,
        minimum_stake: float,
        virtual_protection_enabled: bool = True,
        maximum_recovery_balance_fraction: float = 0.10,
        minimum_balance_reserve: float = 0.50,
    ) -> StakePlan:
        plan = original_plan_stake(
            self,
            managed_account_id=managed_account_id,
            account_id_masked=account_id_masked,
            current_balance=current_balance,
            requested_stake=requested_stake,
            proposal_profit_ratio=proposal_profit_ratio,
            recovery_enabled=recovery_enabled,
            recovery_trigger_losses=recovery_trigger_losses,
            minimum_stake=minimum_stake,
            virtual_protection_enabled=virtual_protection_enabled,
            maximum_recovery_balance_fraction=maximum_recovery_balance_fraction,
            minimum_balance_reserve=minimum_balance_reserve,
        )

        balance = max(0.0, float(current_balance))
        base_stake = ceil_cents(max(float(minimum_stake), float(requested_stake)))
        spendable = max(0.0, balance - float(minimum_balance_reserve))
        base_is_affordable = base_stake <= spendable + 1e-9
        small_account = balance <= SMALL_ACCOUNT_MAX_BALANCE + 1e-9
        waiting_virtual = "virtual protection" in str(plan.reason or "").lower()

        required_recovery = max(
            float(plan.required_recovery_stake or 0.0),
            float(plan.stake or 0.0) if plan.is_recovery else 0.0,
        )
        elevated_recovery = bool(
            plan.is_recovery and required_recovery > base_stake + 0.009
        )
        blocked_recovery = bool(
            plan.is_recovery
            and plan.stake is None
            and float(plan.required_recovery_stake or 0.0) > 0
        )

        # Never bypass the 2-loss virtual guard. While it is active there is no
        # real purchase. Martingale-enabled accounts must either place the single
        # full-debt recovery stake or be blocked by the balance/safety cap; they
        # must not be silently downgraded to a flat base-stake recovery.
        protect_with_base = bool(
            elevated_recovery
            and not recovery_enabled
            and base_is_affordable
            and not waiting_virtual
            and (small_account or blocked_recovery)
        )
        if protect_with_base:
            reason = (
                f"Small-account protection: recovery stake {required_recovery:.2f} was skipped; "
                f"using your base stake {base_stake:.2f}. Recovery debt "
                f"{float(plan.recovery_debt or 0.0):.2f} remains and will be reduced by actual profits."
            )
            notices = getattr(self, "_small_account_protection_notices", None)
            if not isinstance(notices, dict):
                notices = {}
                self._small_account_protection_notices = notices
            notices[int(managed_account_id)] = reason
            self.base.set_managed_account_execution_status(
                int(managed_account_id),
                "base_stake_protection",
                reason,
            )
            return StakePlan(
                base_stake,
                reason,
                is_recovery=True,
                recovery_debt=float(plan.recovery_debt or 0.0),
                required_recovery_stake=required_recovery,
            )

        if plan.stake is not None:
            account = self.base.managed_account(int(managed_account_id)) or {}
            if str(account.get("execution_status") or "").lower() in {
                "base_stake_protection",
                "recovery_pending",
            }:
                self.base.set_managed_account_execution_status(
                    int(managed_account_id),
                    "active",
                    "Account execution is active",
                )
        return plan

    def start_virtual_trade_with_feedback(
        self: RFDir5Repository,
        *,
        managed_account_id: int,
        account_id_masked: str,
        signal: Any,
        configured_stake: float,
        simulated_stake: float,
        expected_payout: float | None,
    ) -> dict[str, Any] | None:
        opened = original_start_virtual_trade(
            self,
            managed_account_id=managed_account_id,
            account_id_masked=account_id_masked,
            signal=signal,
            configured_stake=configured_stake,
            simulated_stake=simulated_stake,
            expected_payout=expected_payout,
        )
        if opened is not None:
            self.base.set_managed_account_execution_status(
                int(managed_account_id),
                "virtual_protection",
                (
                    "2 actual losses triggered protection. Real contracts are being skipped "
                    "until one virtual win confirms recovery."
                ),
            )
        return opened

    def settle_virtual_trades_with_feedback(self: RFDir5Repository, **kwargs: Any) -> list[dict[str, Any]]:
        settled = original_settle_virtual_trades(self, **kwargs)
        for item in settled:
            account_masked = str(item.get("account") or "")
            if not account_masked:
                continue
            with self.database.session() as session:
                state = session.scalar(
                    select(AccountRiskState).where(
                        AccountRiskState.account_id_masked == account_masked
                    )
                )
                managed_id = int(state.managed_account_id) if state is not None else None
            if managed_id is None:
                continue

            protection = item.get("protection") or {}
            mode = str(protection.get("mode") or "")
            virtual_wins = int(protection.get("virtual_wins") or 0)
            result = str(item.get("result") or "").replace("_", " ").lower()
            if mode == RECOVERY_PENDING:
                reason = (
                    "One virtual win confirmed recovery. The next real entry is armed; "
                    "small-account protection will replace any oversized recovery stake with your base stake."
                )
                status = "recovery_pending"
            elif mode == VIRTUAL_MODE:
                reason = (
                    f"Virtual protection active: latest observation {result}; "
                    f"virtual wins {virtual_wins}/1. Real contracts remain skipped."
                )
                status = "virtual_protection"
            else:
                reason = "Virtual protection cleared; account execution is active"
                status = "active"
            self.base.set_managed_account_execution_status(managed_id, status, reason)
        return settled

    async def purchase_accounts_with_feedback(
        self: TradingBot,
        *,
        signal: Any,
        eligible_accounts: list[tuple[str, str]],
        stake_by_token: dict[str, float],
        pre_trade_profit_ratio: float = 0.0,
    ) -> list[dict[str, Any]]:
        transactions = await original_purchase_accounts(
            self,
            signal=signal,
            eligible_accounts=eligible_accounts,
            stake_by_token=stake_by_token,
            pre_trade_profit_ratio=pre_trade_profit_ratio,
        )
        token_by_account = {
            str(account_id): token for token, account_id in eligible_accounts
        }

        paused_tokens: set[str] = set()
        for transaction in transactions:
            account_id = str(transaction.get("account_id") or "") if isinstance(transaction, dict) else ""
            token = token_by_account.get(account_id)
            managed_id = self._managed_account_id_for_token(token) if token else None
            error = transaction.get("error") if isinstance(transaction, dict) else None

            if not error:
                if managed_id is not None:
                    notices = getattr(
                        getattr(self, "rf_repository", None),
                        "_small_account_protection_notices",
                        {},
                    )
                    reason = notices.pop(int(managed_id), "") if isinstance(notices, dict) else ""
                    if reason:
                        # The private buy path sets status=active on success. Restore
                        # the important protection notice after that successful buy.
                        self.repository.set_managed_account_execution_status(
                            int(managed_id),
                            "base_stake_protection",
                            reason,
                        )
                continue

            if not token or managed_id is None:
                continue

            # The WebSocket guard already records the exact insufficient-funds
            # stake and pauses that account. Preserve that more specific reason.
            current = self.repository.managed_account(managed_id) or {}
            current_status = str(current.get("execution_status") or "").lower()
            if current_status in {"insufficient_balance", "purchase_insufficient_balance"}:
                paused_tokens.add(token)
                continue

            status, reason, should_pause = _status_for_purchase_error(error)
            self._set_account_execution_status(managed_id, status, reason)
            if should_pause:
                paused_tokens.add(token)

            code, message = _error_text(error)
            self.logger.warning(
                "ACCOUNT_PURCHASE_NOT_COMPLETED account=%s signal_id=%s code=%s "
                "status=%s reason=%s",
                mask_account_id(account_id),
                getattr(signal, "signal_id", "unknown"),
                code,
                status,
                message,
            )

        if paused_tokens:
            self.valid_clients = [
                item for item in self.valid_clients if item[0] not in paused_tokens
            ]
        return transactions

    TradingBot.validate_accounts = validate_accounts_with_personal_stake
    RFDir5Repository.plan_stake = plan_stake_with_small_account_protection
    RFDir5Repository.start_virtual_trade = start_virtual_trade_with_feedback
    RFDir5Repository.settle_due_virtual_trades = settle_virtual_trades_with_feedback
    TradingBot._purchase_accounts_by_stake = purchase_accounts_with_feedback
    TradingBot._account_execution_feedback_installed = True
