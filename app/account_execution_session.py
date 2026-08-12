from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models import AccountSnapshot
from app.strategy.decision_engine import ProposalEconomics, parse_proposal_economics
from enhanced_bot import (
    is_permanent_credential_error,
    mask_account_id,
    optional_epoch_datetime,
    optional_float,
    sanitize_account_ids,
)


class AccountExecutionPreparationError(RuntimeError):
    """Raised when an enabled account is not safe to scan or purchase for."""


class AccountExecutionError(RuntimeError):
    """Raised when a prepared account cannot complete its exact purchase."""


@dataclass(slots=True)
class AccountExecutionSession:
    """One custom strategy -> one authenticated Deriv account execution session.

    This class deliberately does not call RF/AIDR, cohort, shared-signal, bulk,
    multi-strategy, or rotating-account execution code. It owns the exact runtime
    key, private WebSocket session and durable trade registration for one managed
    account.
    """

    bot: Any
    token: str
    account_id: str
    managed_account_id: int

    def prepare(self) -> tuple[dict[str, Any], Any]:
        profile = dict(getattr(self.bot, "user_profiles", {}).get(self.token, {}) or {})
        profile_managed_id = profile.get("managed_account_id")
        if profile_managed_id in {None, ""} or int(profile_managed_id) != int(
            self.managed_account_id
        ):
            raise AccountExecutionPreparationError(
                "account runtime ownership could not be verified"
            )
        if str(profile.get("account_id") or "").strip() != str(self.account_id).strip():
            raise AccountExecutionPreparationError(
                "authenticated account does not match the runtime account"
            )
        credential = str(self.bot._credential_for_token(self.token) or "").strip()
        if not credential:
            raise AccountExecutionPreparationError(
                "account trading credential is unavailable"
            )
        try:
            state = self.bot._client_state_for_token(
                self.token,
                account_id=self.account_id,
            )
        except KeyError as exc:
            raise AccountExecutionPreparationError(
                "account execution state is not registered"
            ) from exc
        state_managed_id = state.get("managed_account_id")
        if state_managed_id in {None, ""} or int(state_managed_id) != int(
            self.managed_account_id
        ):
            raise AccountExecutionPreparationError(
                "registered account execution state belongs to another account"
            )
        private_session = getattr(self.bot, "sessions", {}).get(self.token)
        if private_session is None or not bool(getattr(private_session, "is_connected", False)):
            raise AccountExecutionPreparationError(
                "authenticated Deriv trading session is not connected"
            )
        if str(getattr(private_session, "account_id", "") or "").strip() != str(
            self.account_id
        ).strip():
            raise AccountExecutionPreparationError(
                "private Deriv session belongs to another account"
            )
        return state, private_session

    def _proposal_request(self, signal: Any, stake: float) -> dict[str, Any]:
        request: dict[str, Any] = {
            "proposal": 1,
            "amount": round(float(stake), 2),
            "basis": "stake",
            "contract_type": str(signal.contract_type),
            "currency": str(self.bot.currency),
            "duration": max(1, int(getattr(signal, "duration_ticks", 1) or 1)),
            "duration_unit": "t",
            "underlying_symbol": str(signal.symbol),
        }
        barrier = str(getattr(signal, "barrier", "") or "").strip()
        if barrier:
            request["barrier"] = barrier
        return request

    async def proposal(
        self,
        signal: Any,
        *,
        stake: float,
        predicted_probability: float,
    ) -> ProposalEconomics:
        self.prepare()
        requested = time.monotonic()
        response = await self.bot.public_client.send_request(
            self._proposal_request(signal, stake)
        )
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

    async def buy_proposal(
        self,
        economics: ProposalEconomics,
    ) -> dict[str, Any]:
        _state, private_session = self.prepare()
        self.bot.logger.info(
            "PURCHASE_EXECUTION_REQUEST managed_id=%s account=%s proposal_id=%s "
            "stake=%.2f transport=account_private_websocket",
            self.managed_account_id,
            mask_account_id(self.account_id),
            economics.proposal_id,
            economics.stake,
        )
        response = await private_session.send_request(
            {
                "buy": str(economics.proposal_id),
                "price": round(float(economics.stake), 2),
            }
        )
        if "error" in response:
            error = response.get("error") or {}
            message = sanitize_account_ids(
                str(error.get("message") or "Deriv purchase failed")
            )
            permanent = is_permanent_credential_error(error)
            self.bot._set_account_execution_status(
                self.managed_account_id,
                "credential_error" if permanent else "error",
                message,
            )
            if permanent:
                self.bot.valid_clients = [
                    item for item in self.bot.valid_clients if item[0] != self.token
                ]
            raise AccountExecutionError(message)
        buy = dict(response.get("buy") or {})
        contract_id = buy.get("contract_id")
        if not contract_id:
            raise AccountExecutionError("Deriv buy response did not include a contract ID")
        return buy

    def _current_balance(self) -> float:
        masked = mask_account_id(self.account_id)
        with self.bot.repository.database.session() as session:
            snapshot = session.scalar(
                select(AccountSnapshot).where(
                    AccountSnapshot.run_id == self.bot.repository.run_id,
                    AccountSnapshot.account_id_masked == masked,
                )
            )
        return float(snapshot.balance if snapshot is not None else 0.0)

    async def register_purchase(
        self,
        *,
        signal: Any,
        buy: dict[str, Any],
        stake: float,
        profit_ratio: float,
        purchase_requested_at: datetime,
    ) -> int:
        state, private_session = self.prepare()
        contract_id = int(buy["contract_id"])
        transaction_id = (
            buy.get("transaction_id")
            or (buy.get("transaction_ids") or {}).get("buy")
            or contract_id
        )
        state["current_stake"] = round(float(stake), 2)
        state["last_profit_ratio"] = float(profit_ratio)

        try:
            self.bot.repository.register_purchase(
                signal_id=str(signal.signal_id),
                contract_id=str(contract_id),
                transaction_id=str(transaction_id),
                account_id=self.account_id,
                purchase_time=purchase_requested_at,
                aligned_with_signal=True,
                buy_price=optional_float(buy.get("buy_price")),
                payout=optional_float(buy.get("payout")),
                provider_purchase_time=optional_epoch_datetime(buy.get("purchase_time")),
                provider_start_time=optional_epoch_datetime(
                    buy.get("start_time") or buy.get("date_start")
                ),
                contract_duration=max(
                    1, int(getattr(signal, "duration_ticks", 1) or 1)
                ),
                contract_duration_unit="t",
                managed_account_id=self.managed_account_id,
                bulk_batch_id=None,
            )
        except Exception:
            self.bot.unregistered_contracts.add(contract_id)
            private_session.pending_contracts.add(contract_id)
            self.bot.pending_contract_started_at[contract_id] = purchase_requested_at
            raise

        self.bot.pending_contracts_for_current_cycle.add(contract_id)
        self.bot.contract_signal_ids[contract_id] = str(signal.signal_id)
        self.bot.contract_symbols[contract_id] = str(signal.symbol)
        self.bot.pending_by_signal.setdefault(str(signal.signal_id), set()).add(contract_id)
        self.bot.outcomes_by_signal.setdefault(str(signal.signal_id), {})
        self.bot.signal_symbols[str(signal.signal_id)] = str(signal.symbol)
        private_session.pending_contracts.add(contract_id)
        self.bot.pending_contract_started_at[contract_id] = purchase_requested_at

        await private_session.subscribe_contract(contract_id)
        self.bot.logger.info(
            "PURCHASE_CONFIRMED signal_id=%s managed_id=%s account=%s contract_id=%s "
            "contract_type=%s barrier=%s duration_ticks=%s stake=%.2f",
            signal.signal_id,
            self.managed_account_id,
            mask_account_id(self.account_id),
            contract_id,
            signal.contract_type,
            getattr(signal, "barrier", "") or "-",
            max(1, int(getattr(signal, "duration_ticks", 1) or 1)),
            stake,
        )
        try:
            self.bot._on_account_contract_registered(
                self.token,
                self.account_id,
                contract_id,
                float(stake),
            )
        except Exception:
            self.bot.logger.exception(
                "CUSTOM_ACCOUNT_POST_REGISTER_FAILED managed_id=%s contract_id=%s",
                self.managed_account_id,
                contract_id,
            )
        asyncio.create_task(
            self.bot._cycle_timeout_watchdog(str(signal.signal_id), [contract_id])
        )
        return contract_id

    async def execute_real(
        self,
        signal: Any,
        *,
        predicted_probability: float,
        virtual_protection_enabled: bool,
    ) -> int:
        state, _private_session = self.prepare()
        configured_stake = round(float(state.get("base_stake") or 0.50), 2)
        first_economics = await self.proposal(
            signal,
            stake=configured_stake,
            predicted_probability=predicted_probability,
        )
        profit_ratio = float(first_economics.potential_profit) / float(
            first_economics.stake
        )
        balance = self._current_balance()
        plan = self.bot.rf_repository.plan_stake(
            managed_account_id=self.managed_account_id,
            account_id_masked=mask_account_id(self.account_id),
            current_balance=balance,
            requested_stake=configured_stake,
            proposal_profit_ratio=profit_ratio,
            recovery_enabled=bool(state.get("martingale_enabled", True)),
            recovery_trigger_losses=2,
            minimum_stake=configured_stake,
            virtual_protection_enabled=bool(virtual_protection_enabled),
        )
        if plan.stake is None:
            raise AccountExecutionError(plan.reason or "Account stake plan rejected execution")
        final_stake = round(float(plan.stake), 2)
        economics = first_economics
        if abs(final_stake - float(first_economics.stake)) > 0.001:
            economics = await self.proposal(
                signal,
                stake=final_stake,
                predicted_probability=predicted_probability,
            )
            profit_ratio = float(economics.potential_profit) / float(economics.stake)

        signal.proposal_ask_price = float(economics.stake)
        signal.proposal_payout = float(economics.payout)
        signal.break_even_probability = float(economics.break_even_probability)
        signal.validated_edge = float(predicted_probability) - float(
            economics.break_even_probability
        )
        self.bot.repository.record_proposal(signal, economics)
        if bool(plan.is_recovery):
            self.bot.rf_repository.mark_recovery_attempt_started(self.managed_account_id)

        requested_at = datetime.now(timezone.utc)
        buy = await self.buy_proposal(economics)
        return await self.register_purchase(
            signal=signal,
            buy=buy,
            stake=final_stake,
            profit_ratio=profit_ratio,
            purchase_requested_at=requested_at,
        )
