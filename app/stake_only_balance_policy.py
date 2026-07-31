from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models import AccountRiskState, utc_now
from app.recovery import calculate_recovery_stake, ceil_cents
from app.repositories.rf_dir5_repository import (
    RECOVERY_PENDING,
    RFDir5Repository,
    StakePlan,
    VIRTUAL_WAITING_FOR_WIN,
)
from enhanced_bot import TradingBot, mask_account_id


PROVIDER_MINIMUM_STAKE = 0.35


def _positive_amount(value: Any, fallback: float) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = float(fallback)
    return amount if amount > 0 else float(fallback)


def _configured_stake(profile: dict[str, Any], account: dict[str, Any]) -> float:
    requested = (
        profile.get("stake_amount")
        or account.get("stake_amount")
        or PROVIDER_MINIMUM_STAKE
    )
    return ceil_cents(
        max(
            PROVIDER_MINIMUM_STAKE,
            _positive_amount(requested, PROVIDER_MINIMUM_STAKE),
        )
    )


def install_stake_only_balance_policy() -> None:
    """Require only the selected stake and let Deriv reject later insufficient buys.

    Account admission checks no longer add a safety reserve. Once an account has
    been admitted, stake planning does not block a provider request because the
    current balance is below a recovery stake or a configured reserve. Virtual
    protection remains unchanged and can still deliberately suppress real buys.
    """

    if getattr(TradingBot, "_stake_only_balance_policy_installed", False):
        return

    current_validate_accounts = TradingBot.validate_accounts
    current_settle_virtual_trades = RFDir5Repository.settle_due_virtual_trades

    async def validate_accounts_stake_only(self: TradingBot) -> None:
        await current_validate_accounts(self)
        rescued: list[tuple[str, str]] = []

        for token in list(getattr(self, "tokens", []) or []):
            profile = (getattr(self, "user_profiles", {}) or {}).get(token, {})
            managed_id = self._managed_account_id_for_token(token)
            account_id = str(profile.get("account_id") or "").strip()
            if managed_id is None or not account_id:
                continue

            account = self.repository.managed_account(managed_id) or {}
            status = str(account.get("execution_status") or "").lower()
            if status != "insufficient_balance":
                continue

            summary = self.repository.account_summary(
                account_id,
                managed_account_id=managed_id,
            )
            balance = max(0.0, float(summary.get("balance") or 0.0))
            stake = _configured_stake(profile, account)

            if balance + 1e-9 < stake:
                self.repository.set_managed_account_execution_status(
                    int(managed_id),
                    "insufficient_balance",
                    (
                        f"Balance {balance:.2f} is below the selected stake {stake:.2f}. "
                        "No additional reserve is required."
                    ),
                )
                continue

            self.repository.update_managed_account(int(managed_id), enabled=True)
            self.repository.set_managed_account_execution_status(
                int(managed_id),
                "connecting",
                (
                    f"Balance {balance:.2f} supports the selected stake {stake:.2f}; "
                    "no recovery or safety reserve is required"
                ),
            )
            rescued.append((token, account_id))
            self.logger.warning(
                "STAKE_ONLY_ACCOUNT_ADMITTED account=%s balance=%.2f stake=%.2f "
                "reserve_required=false",
                mask_account_id(account_id),
                balance,
                stake,
            )

        if rescued:
            combined = list(getattr(self, "valid_clients", []) or []) + rescued
            deduplicated: list[tuple[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for token, account_id in combined:
                key = (str(token), str(account_id))
                if key in seen:
                    continue
                seen.add(key)
                deduplicated.append(key)
            self.valid_clients = deduplicated
            self._sync_running_status_after_validation()

    def plan_stake_without_balance_reserve(
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
        # These legacy arguments remain in the public method signature for callers,
        # but neither is allowed to block a provider purchase under this policy.
        del recovery_trigger_losses
        del maximum_recovery_balance_fraction
        del minimum_balance_reserve

        today = datetime.now(timezone.utc).date().isoformat()
        balance = max(0.0, float(current_balance))
        with self.database.session() as session:
            state = session.get(
                AccountRiskState,
                int(managed_account_id),
                with_for_update=True,
            )
            if state is None:
                state = AccountRiskState(
                    managed_account_id=int(managed_account_id),
                    account_id_masked=str(account_id_masked or ""),
                    trading_day=today,
                    daily_start_balance=balance,
                    session_profit=0.0,
                    consecutive_losses=0,
                    recovery_loss_debt=0.0,
                    recovery_pending=False,
                    recovery_attempt_active=False,
                    equity_high_water=balance,
                )
                session.add(state)
            elif account_id_masked and state.account_id_masked != account_id_masked:
                state.account_id_masked = str(account_id_masked)
            elif state.trading_day != today:
                state.trading_day = today
                state.daily_start_balance = balance
                state.session_profit = 0.0
                state.equity_high_water = balance
                state.consecutive_losses = 0
                state.recovery_loss_debt = 0.0
                state.recovery_pending = False
                state.recovery_attempt_active = False
                state.recovery_pending_since = None
                state.protection_mode = "NORMAL_MODE"
                state.entered_virtual_mode_at = None
                state.virtual_observation_count = 0
                state.virtual_win_count = 0
                state.virtual_loss_count = 0
                state.current_virtual_loss_streak = 0

            state.equity_high_water = max(
                float(state.equity_high_water or 0.0),
                balance,
            )
            if (
                virtual_protection_enabled
                and state.protection_mode == VIRTUAL_WAITING_FOR_WIN
            ):
                state.updated_at = utc_now()
                return StakePlan(
                    None,
                    "virtual protection waiting for virtual wins; debt retained",
                    is_recovery=bool(
                        recovery_enabled
                        and state.recovery_pending
                        and state.recovery_loss_debt > 0
                    ),
                    recovery_debt=float(state.recovery_loss_debt or 0.0),
                )

            base_stake = ceil_cents(
                max(float(minimum_stake), float(requested_stake))
            )
            is_recovery = bool(
                state.recovery_pending
                and not state.recovery_attempt_active
                and state.recovery_loss_debt > 0
            )
            required_recovery_stake = 0.0
            target_stake = base_stake

            if is_recovery and recovery_enabled:
                calculation = calculate_recovery_stake(
                    base_stake=base_stake,
                    recovery_debt=float(state.recovery_loss_debt or 0.0),
                    pre_trade_profit_ratio=proposal_profit_ratio,
                    minimum_stake=minimum_stake,
                )
                required_recovery_stake = calculation.required_recovery_stake
                target_stake = calculation.requested_stake
                if not calculation.allowed:
                    state.updated_at = utc_now()
                    return StakePlan(
                        None,
                        calculation.reason,
                        is_recovery=True,
                        recovery_debt=float(state.recovery_loss_debt or 0.0),
                        required_recovery_stake=required_recovery_stake,
                    )

            state.updated_at = utc_now()
            return StakePlan(
                target_stake,
                "",
                is_recovery=is_recovery,
                recovery_debt=float(state.recovery_loss_debt or 0.0),
                required_recovery_stake=required_recovery_stake,
            )

    def settle_virtual_trades_stake_only(
        self: RFDir5Repository,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        settled = current_settle_virtual_trades(self, **kwargs)
        for item in settled:
            protection = item.get("protection") or {}
            if str(protection.get("mode") or "") != RECOVERY_PENDING:
                continue
            account_masked = str(item.get("account") or "")
            if not account_masked:
                continue
            with self.database.session() as session:
                state = session.scalar(
                    select(AccountRiskState).where(
                        AccountRiskState.account_id_masked == account_masked
                    )
                )
                managed_id = (
                    int(state.managed_account_id) if state is not None else None
                )
            if managed_id is None:
                continue
            self.base.set_managed_account_execution_status(
                managed_id,
                "recovery_pending",
                (
                    "2 consecutive virtual wins confirmed recovery. The next real "
                    "entry is armed without a reserved-balance requirement; Deriv "
                    "will return the provider error if the current balance cannot "
                    "cover the requested stake."
                ),
            )
        return settled

    TradingBot.validate_accounts = validate_accounts_stake_only
    RFDir5Repository.plan_stake = plan_stake_without_balance_reserve
    RFDir5Repository.settle_due_virtual_trades = settle_virtual_trades_stake_only
    TradingBot._stake_only_balance_policy_installed = True
