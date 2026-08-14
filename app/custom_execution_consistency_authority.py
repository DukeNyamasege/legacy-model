from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import select

from app import custom_strategy_connection_stampede_guard as connection_guard
from app import custom_strategy_direct_runtime as direct_runtime
from app import custom_strategy_result_router as result_router
from app import manual_martingale_v2 as manual
from app import netlify_worker_bridge as bridge
from app.account_execution_session import AccountExecutionError, AccountExecutionSession
from app.models import AccountRiskState, Trade, utc_now
from app.repositories.rf_dir5_repository import (
    NORMAL_MODE,
    RFDir5Repository,
    StakePlan,
    VIRTUAL_WAITING_FOR_WIN,
)
from app.rf_dir5_bot import RFDir5TradingBot
from app.recovery import ceil_cents
from app.strategy.decision_engine import ProposalEconomics
from enhanced_bot import is_permanent_credential_error, mask_account_id, sanitize_account_ids


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_ORIGINAL_BUY: Any = None
_ORIGINAL_EXECUTE_REAL: Any = None
_ORIGINAL_FAIL_HANDLER: Any = None
_ORIGINAL_PLAN_STAKE: Any = None
_ORIGINAL_RECORD_OUTCOME: Any = None
_ORIGINAL_EXECUTE_ACCOUNT: Any = None
_ORIGINAL_HANDLE_CONTRACT: Any = None
_ORIGINAL_OPEN_ACTUAL: Any = None

# A request deadline is still required to detect a dead transport. Reaching this
# deadline is *not* a trading stop: it starts account-scoped reconnect/reconciliation.
_PRIVATE_FINANCIAL_REQUEST_SECONDS = 15.0
_AMBIGUOUS_RECONCILE_SECONDS = 75.0

_STAKE_POLICY_MARKERS = (
    "insufficient account balance",
    "recovery stake",
    "stake plan rejected execution",
    "debt retained",
    "exceeds account safety cap",
)


class AmbiguousPurchaseTimeout(AccountExecutionError):
    """The BUY may have reached Deriv even though its acknowledgement was lost."""

    def __init__(self, economics: ProposalEconomics, requested_at: datetime) -> None:
        super().__init__(
            "Purchase acknowledgement was not received; reconnecting and reconciling before any further real trade"
        )
        self.economics = economics
        self.requested_at = requested_at


def _account_enabled(bot: RFDir5TradingBot, managed_id: int) -> bool:
    try:
        return bool((bot.repository.managed_account(int(managed_id)) or {}).get("enabled"))
    except Exception:
        return False


def _session_for(bot: RFDir5TradingBot, managed_id: int) -> Any | None:
    return connection_guard._private_session_for_account(bot, int(managed_id))


def _ambiguity_set(bot: RFDir5TradingBot) -> set[int]:
    values = getattr(bot, "_custom_purchase_reconciliation_pending", None)
    if not isinstance(values, set):
        values = set()
        bot._custom_purchase_reconciliation_pending = values
    return values


def _reconciliation_tasks(bot: RFDir5TradingBot) -> dict[int, asyncio.Task[Any]]:
    tasks = getattr(bot, "_custom_purchase_reconciliation_tasks", None)
    if not isinstance(tasks, dict):
        tasks = {}
        bot._custom_purchase_reconciliation_tasks = tasks
    return tasks


def _dashboard_wakeup(bot: RFDir5TradingBot) -> None:
    try:
        bridge._schedule_dashboard_wakeup(bot)
    except Exception:
        pass


def _request_private_reconnect(bot: RFDir5TradingBot, managed_id: int, reason: str) -> None:
    """Reconnect one account only; never convert a transient execution fault to Stop."""

    if not _account_enabled(bot, int(managed_id)):
        return
    bot._set_account_execution_status(
        int(managed_id),
        "reconnecting",
        "Execution transport interrupted; reconnecting automatically. Auto Trading remains active.",
    )
    session = _session_for(bot, int(managed_id))
    websocket = getattr(session, "ws", None) if session is not None else None
    if websocket is not None:
        try:
            asyncio.get_running_loop().create_task(
                websocket.close(code=1012, reason="custom_execution_reconnect"),
                name=f"custom_execution_reconnect_{int(managed_id)}",
            )
        except (RuntimeError, AttributeError):
            pass
    connection_guard._schedule_targeted_runtime_repair(bot, int(managed_id))
    bot.logger.warning(
        "CUSTOM_EXECUTION_RECONNECT managed_id=%s lifecycle_stop=false "
        "duplicate_buy_retry=false reason=%s",
        int(managed_id),
        str(reason or "execution transport fault")[:140],
    )
    _dashboard_wakeup(bot)


def _request_public_reconnect(bot: RFDir5TradingBot, reason: str) -> None:
    client = getattr(bot, "public_client", None)
    if client is None or getattr(client, "ws", None) is None:
        return
    try:
        asyncio.get_running_loop().create_task(
            client.request_reconnect("custom_execution_error"),
            name="custom_execution_public_reconnect",
        )
    except (RuntimeError, AttributeError):
        pass
    bot.logger.warning(
        "CUSTOM_PUBLIC_EXECUTION_RECONNECT lifecycle_stop=false reason=%s",
        str(reason or "public execution transport fault")[:140],
    )


def _stake_policy_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    return any(marker in text for marker in _STAKE_POLICY_MARKERS)


def _latest_actual_stake(repository: RFDir5Repository, managed_id: int) -> float | None:
    """Return the previous actual purchased stake; virtual observations never count."""

    with repository.database.session() as session:
        row = session.scalar(
            select(Trade)
            .where(
                Trade.managed_account_id == int(managed_id),
                Trade.buy_price.is_not(None),
                Trade.settlement_time.is_not(None),
            )
            .order_by(Trade.purchase_time.desc(), Trade.id.desc())
            .limit(1)
        )
    if row is None or row.buy_price is None:
        return None
    try:
        value = float(row.buy_price)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _exact_split_stake(
    *,
    base_stake: float,
    recovery_debt: float,
    proposal_profit_ratio: float,
    remaining_parts: int,
) -> tuple[float, float]:
    """Recover only the outstanding actual loss debt across configured successful parts."""

    base = ceil_cents(max(0.35, float(base_stake or 0.0)))
    debt = max(0.0, float(recovery_debt or 0.0))
    ratio = float(proposal_profit_ratio or 0.0)
    parts = max(1, min(3, int(remaining_parts or 1)))
    if debt <= 0.009 or ratio <= 0:
        return base, base
    full_exact_stake = ceil_cents(max(base, debt / ratio))
    target_profit = debt / parts
    part_stake = ceil_cents(max(base, target_profit / ratio))
    return part_stake, full_exact_stake


def _finish_split_without_hidden_cleanup(
    repository: RFDir5Repository,
    managed_id: int,
    result: dict[str, Any],
) -> None:
    """Configured N successful parts are final; residual is realized P/L, not hidden recovery."""

    residual = round(max(0.0, float(result.get("recovery_loss_debt") or 0.0)), 2)
    manual._reset_manual_cycle(repository, int(managed_id))
    result.update(
        {
            "manual_split_remaining": 0,
            "manual_split_cleanup": False,
            "manual_split_residual_unrecovered": residual,
            "recovery_loss_debt": 0.0,
            "recovery_pending": False,
            "recovery_attempt_active": False,
            "protection_mode": NORMAL_MODE,
            "raw_protection_state": NORMAL_MODE,
        }
    )
    try:
        result_router._ROUTE_STATE[
            result_router._state_key(repository.database, int(managed_id))
        ] = result_router.AFTER_WIN
        result["custom_result_route"] = result_router.AFTER_WIN
    except Exception:
        pass
    LOGGER.warning(
        "CUSTOM_SPLIT_CONFIGURED_PARTS_COMPLETE managed_id=%s residual_not_recovered=%.2f "
        "extra_cleanup_trade=false session_profit_preserved=true",
        int(managed_id),
        residual,
    )


def _clear_unconfirmed_recovery_attempt(repository: RFDir5Repository, managed_id: int) -> None:
    """A BUY that is definitively absent must not leave recovery marked in-flight."""

    with repository.database.session() as session:
        state = session.get(AccountRiskState, int(managed_id), with_for_update=True)
        if state is None:
            return
        if state.recovery_attempt_active:
            state.recovery_attempt_active = False
            state.recovery_pending = bool(float(state.recovery_loss_debt or 0.0) >= 0.01)
            state.updated_at = utc_now()


def _candidate_from_transactions(
    transactions: list[dict[str, Any]],
    *,
    economics: ProposalEconomics,
    requested_at: datetime,
) -> dict[str, Any] | None:
    requested_epoch = int(requested_at.timestamp())
    candidates: list[dict[str, Any]] = []
    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        try:
            buy_price = float(tx.get("buy_price") or 0.0)
            purchase_time = int(float(tx.get("purchase_time") or 0))
        except (TypeError, ValueError):
            continue
        if abs(buy_price - float(economics.stake)) > 0.011:
            continue
        if purchase_time and abs(purchase_time - requested_epoch) > 45:
            continue
        candidates.append(tx)
    if len(candidates) == 1:
        return candidates[0]
    return None


async def _find_ambiguous_purchase(
    execution: AccountExecutionSession,
    *,
    economics: ProposalEconomics,
    requested_at: datetime,
) -> dict[str, Any] | None:
    """Use authenticated portfolio/profit history after reconnect; never repeat BUY."""

    _state, private = execution.prepare()
    for request in (
        {"portfolio": 1},
        {
            "profit_table": 1,
            "limit": 50,
            "sort": "DESC",
            "date_from": str(max(0, int(requested_at.timestamp()) - 60)),
        },
    ):
        response = await private.send_request(request)
        if "error" in response:
            continue
        if "portfolio" in response:
            transactions = list((response.get("portfolio") or {}).get("contracts") or [])
        else:
            transactions = list((response.get("profit_table") or {}).get("transactions") or [])
        candidate = _candidate_from_transactions(
            transactions,
            economics=economics,
            requested_at=requested_at,
        )
        if candidate is not None and candidate.get("contract_id"):
            return candidate
    return None


async def _reconcile_ambiguous_purchase(
    execution: AccountExecutionSession,
    signal: Any,
    timeout: AmbiguousPurchaseTimeout,
) -> None:
    bot: RFDir5TradingBot = execution.bot
    managed_id = int(execution.managed_account_id)
    started = asyncio.get_running_loop().time()
    try:
        while bot.is_running and _account_enabled(bot, managed_id):
            session = _session_for(bot, managed_id)
            if session is None or not bool(getattr(session, "is_connected", False)):
                await asyncio.sleep(0.5)
                continue
            try:
                buy = await _find_ambiguous_purchase(
                    execution,
                    economics=timeout.economics,
                    requested_at=timeout.requested_at,
                )
            except Exception:
                buy = None
            if buy is not None:
                profit_ratio = float(timeout.economics.potential_profit) / float(
                    timeout.economics.stake
                )
                contract_id = await execution.register_purchase(
                    signal=signal,
                    buy=buy,
                    stake=float(timeout.economics.stake),
                    profit_ratio=profit_ratio,
                    purchase_requested_at=timeout.requested_at,
                )
                bot.repository.mark_signal(
                    signal.signal_id,
                    status="PURCHASE_CONFIRMED_RECONCILED",
                    purchase_requested=True,
                    purchase_confirmed=True,
                )
                bot._set_account_execution_status(
                    managed_id,
                    "running",
                    f"Contract {contract_id} was reconciled after transport interruption; settlement monitoring is active",
                )
                bot.logger.warning(
                    "CUSTOM_AMBIGUOUS_BUY_RECONCILED managed_id=%s contract_id=%s "
                    "duplicate_buy_retry=false",
                    managed_id,
                    contract_id,
                )
                return

            elapsed = asyncio.get_running_loop().time() - started
            # A successful authenticated history query with no unique matching
            # contract is repeated for a bounded observation window. We never BUY
            # again inside this ambiguity window.
            if elapsed >= _AMBIGUOUS_RECONCILE_SECONDS:
                _clear_unconfirmed_recovery_attempt(bot.rf_repository, managed_id)
                bot._set_account_execution_status(
                    managed_id,
                    "waiting_for_condition",
                    "No matching Deriv purchase was found after reconciliation; waiting for a fresh qualifying signal.",
                )
                bot.logger.warning(
                    "CUSTOM_AMBIGUOUS_BUY_NOT_FOUND managed_id=%s observation_seconds=%.1f "
                    "duplicate_buy_retry=false lifecycle_stop=false",
                    managed_id,
                    elapsed,
                )
                return
            await asyncio.sleep(1.0)
    finally:
        _ambiguity_set(bot).discard(managed_id)
        _reconciliation_tasks(bot).pop(managed_id, None)
        _dashboard_wakeup(bot)


def _schedule_ambiguous_reconciliation(
    execution: AccountExecutionSession,
    signal: Any,
    timeout: AmbiguousPurchaseTimeout,
) -> None:
    bot: RFDir5TradingBot = execution.bot
    managed_id = int(execution.managed_account_id)
    pending = _ambiguity_set(bot)
    pending.add(managed_id)
    tasks = _reconciliation_tasks(bot)
    current = tasks.get(managed_id)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(
        _reconcile_ambiguous_purchase(execution, signal, timeout),
        name=f"custom_ambiguous_purchase_reconcile_{managed_id}",
    )
    tasks[managed_id] = task


def install_custom_execution_consistency_authority() -> None:
    """Final Custom Strategy authority for transport, Virtual Hook and recovery sizing."""

    global _INSTALLED, _ORIGINAL_BUY, _ORIGINAL_EXECUTE_REAL, _ORIGINAL_FAIL_HANDLER
    global _ORIGINAL_PLAN_STAKE, _ORIGINAL_RECORD_OUTCOME, _ORIGINAL_EXECUTE_ACCOUNT
    global _ORIGINAL_HANDLE_CONTRACT, _ORIGINAL_OPEN_ACTUAL
    if _INSTALLED:
        return

    _ORIGINAL_BUY = AccountExecutionSession.buy_proposal
    _ORIGINAL_EXECUTE_REAL = AccountExecutionSession.execute_real
    _ORIGINAL_FAIL_HANDLER = direct_runtime._fail_closed
    _ORIGINAL_PLAN_STAKE = RFDir5Repository.plan_stake
    _ORIGINAL_RECORD_OUTCOME = RFDir5Repository.record_account_outcome
    _ORIGINAL_EXECUTE_ACCOUNT = direct_runtime._execute_for_account
    _ORIGINAL_HANDLE_CONTRACT = RFDir5TradingBot.handle_contract_update
    _ORIGINAL_OPEN_ACTUAL = direct_runtime._account_has_open_actual

    # The earlier bridge used a 4-second private request SLA. Keep a deadline for
    # dead-transport detection, but never interpret that deadline as a trading Stop.
    bridge._private_request_timeout = lambda: _PRIVATE_FINANCIAL_REQUEST_SECONDS

    # Split is the only Custom mode allowed to size from accumulated actual loss.
    # No percentage/buffer is added beyond debt / current proposal profit ratio.
    manual.split_recovery_stake = _exact_split_stake

    async def buy_with_reconnect_not_stop(
        self: AccountExecutionSession,
        economics: ProposalEconomics,
    ) -> dict[str, Any]:
        _state, private = self.prepare()
        requested_at = datetime.now(timezone.utc)
        self.bot.logger.info(
            "PURCHASE_EXECUTION_REQUEST managed_id=%s account=%s proposal_id=%s "
            "stake=%.2f transport=account_private_websocket timeout_policy=reconcile_never_stop",
            self.managed_account_id,
            mask_account_id(self.account_id),
            economics.proposal_id,
            economics.stake,
        )
        response = await private.send_request(
            {
                "buy": str(economics.proposal_id),
                "price": round(float(economics.stake), 2),
            }
        )
        if "error" in response:
            error = response.get("error") or {}
            message = sanitize_account_ids(
                str(error.get("message") or "Deriv purchase transport failed")
            )
            code = str(error.get("code") or "").strip().upper()
            if is_permanent_credential_error(error):
                self.bot._set_account_execution_status(
                    self.managed_account_id,
                    "credential_error",
                    message,
                )
                self.bot.valid_clients = [
                    item for item in self.bot.valid_clients if item[0] != self.token
                ]
                raise AccountExecutionError(message)

            _request_private_reconnect(self.bot, self.managed_account_id, message)
            if code == "TIMEOUT" or "timed out" in message.lower():
                raise AmbiguousPurchaseTimeout(economics, requested_at)
            raise AccountExecutionError(
                f"Execution transport error; reconnecting automatically: {message}"
            )

        buy = dict(response.get("buy") or {})
        if not buy.get("contract_id"):
            _request_private_reconnect(
                self.bot,
                self.managed_account_id,
                "Deriv buy response did not include a contract ID",
            )
            raise AccountExecutionError(
                "Execution response was incomplete; reconnecting automatically"
            )
        return buy

    async def execute_real_with_ambiguity_lock(
        self: AccountExecutionSession,
        signal: Any,
        *,
        predicted_probability: float,
        virtual_protection_enabled: bool,
    ) -> int:
        original = _ORIGINAL_EXECUTE_REAL
        if original is None:
            raise AccountExecutionError("Real execution authority is unavailable")
        try:
            return await original(
                self,
                signal,
                predicted_probability=predicted_probability,
                virtual_protection_enabled=virtual_protection_enabled,
            )
        except AmbiguousPurchaseTimeout as exc:
            _schedule_ambiguous_reconciliation(self, signal, exc)
            raise AccountExecutionError(
                "Purchase acknowledgement pending reconciliation; Auto Trading remains active and duplicate BUY is blocked"
            ) from exc

    def plan_stake_with_pure_multiplier(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> StakePlan:
        original = _ORIGINAL_PLAN_STAKE
        if original is None:
            raise RuntimeError("Stake planner is unavailable")
        plan = original(self, *args, **kwargs)
        try:
            managed_id = int(kwargs.get("managed_account_id"))
            settings = manual.read_manual_martingale_settings(self, managed_id)
            family = manual._manual_family(self, managed_id)
        except Exception:
            return plan
        if family == "system" or str(settings.get("mode")) != manual.MULTIPLIER_MODE:
            return plan

        snapshot = manual._account_snapshot(self, managed_id)
        if not manual._account_running(snapshot):
            return plan
        if snapshot.get("mode") == VIRTUAL_WAITING_FOR_WIN:
            return plan
        if not manual._is_recovery_snapshot(snapshot):
            return plan

        base = ceil_cents(
            max(
                0.35,
                float(kwargs.get("minimum_stake") or 0.0),
                float(kwargs.get("requested_stake") or 0.0),
            )
        )
        previous_stake = _latest_actual_stake(self, managed_id) or base
        multiplier = float(settings.get("multiplier") or manual.DEFAULT_MULTIPLIER)
        target = ceil_cents(previous_stake * multiplier)
        balance = max(0.0, float(kwargs.get("current_balance") or 0.0))
        reserve = max(0.0, float(kwargs.get("minimum_balance_reserve") or 0.0))
        spendable = max(0.0, balance - reserve)
        if target > spendable + 1e-9:
            return StakePlan(
                None,
                f"multiplier stake {target:.2f} exceeds spendable balance {spendable:.2f}",
                is_recovery=True,
                recovery_debt=float(snapshot.get("debt") or 0.0),
                required_recovery_stake=target,
            )

        manual._mark_recovery_attempt(self, managed_id)
        LOGGER.info(
            "CUSTOM_PURE_MULTIPLIER_STAKE managed_id=%s previous_actual_stake=%.2f "
            "multiplier=%.2f next_stake=%.2f debt_sizing=false payout_sizing=false",
            managed_id,
            previous_stake,
            multiplier,
            target,
        )
        return StakePlan(
            target,
            f"pure multiplier: {previous_stake:.2f} x {multiplier:.2f} = {target:.2f}",
            is_recovery=True,
            recovery_debt=float(snapshot.get("debt") or 0.0),
            required_recovery_stake=target,
        )

    def record_outcome_with_immediate_virtual_state(
        self: RFDir5Repository,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        original = _ORIGINAL_RECORD_OUTCOME
        if original is None:
            raise RuntimeError("Settlement recorder is unavailable")
        result = original(self, *args, **kwargs)
        try:
            managed_id = int(kwargs.get("managed_account_id"))
        except (TypeError, ValueError):
            return result

        if (
            str(result.get("raw_protection_state") or "") == VIRTUAL_WAITING_FOR_WIN
            and bool(result.get("protection_state_changed"))
        ):
            try:
                self.base.set_managed_account_execution_status(
                    managed_id,
                    "virtual_protection",
                    "Virtual Hook is active now; qualifying trades are zero-stake mirrors until the configured virtual-win requirement is met.",
                )
            except Exception:
                pass
            LOGGER.warning(
                "CUSTOM_VIRTUAL_HOOK_ENTERED managed_id=%s status_persisted_immediately=true",
                managed_id,
            )

        try:
            settings = manual.read_manual_martingale_settings(self, managed_id)
            family = manual._manual_family(self, managed_id)
        except Exception:
            return result
        if (
            family != "system"
            and str(settings.get("mode")) == manual.SPLIT_MODE
            and bool(result.get("manual_split_cleanup"))
        ):
            _finish_split_without_hidden_cleanup(self, managed_id, result)
        return result

    async def execute_account_with_virtual_wakeup(
        bot: RFDir5TradingBot,
        item: Any,
        *,
        signal: Any,
    ) -> None:
        original = _ORIGINAL_EXECUTE_ACCOUNT
        if original is None:
            return
        await original(bot, item, signal=signal)
        protection = bot.rf_repository.virtual_protection_for_account(
            managed_account_id=int(item.managed_id),
            account_id_masked="",
        )
        if str(protection.get("mode") or "") == "VIRTUAL_MODE":
            bot._set_account_execution_status(
                int(item.managed_id),
                "virtual_protection",
                "Virtual Hook is active; the current qualifying observation has zero financial stake.",
            )
            bot.logger.info(
                "CUSTOM_VIRTUAL_HOOK_VISIBLE managed_id=%s dashboard_wakeup=true",
                int(item.managed_id),
            )
            _dashboard_wakeup(bot)

    async def handle_contract_with_virtual_wakeup(
        self: RFDir5TradingBot,
        token: str,
        contract_id: int,
        contract: dict[str, Any],
    ) -> Any:
        original = _ORIGINAL_HANDLE_CONTRACT
        if original is None:
            return None
        result = await original(self, token, contract_id, contract)
        managed_id = self._managed_account_id_for_token(token)
        if managed_id is not None:
            protection = self.rf_repository.virtual_protection_for_account(
                managed_account_id=int(managed_id),
                account_id_masked="",
            )
            if str(protection.get("mode") or "") == "VIRTUAL_MODE":
                self._set_account_execution_status(
                    int(managed_id),
                    "virtual_protection",
                    "Virtual Hook is active now; waiting for the next qualifying zero-stake mirror.",
                )
                _dashboard_wakeup(self)
        return result

    def open_actual_including_ambiguity(item: Any) -> bool:
        managed_id = int(getattr(item, "managed_id", -1))
        if managed_id in _ambiguity_set(item.execution.bot):
            return True
        original = _ORIGINAL_OPEN_ACTUAL
        return bool(original(item)) if original is not None else False

    def reconnect_instead_of_execution_stop(
        bot: RFDir5TradingBot,
        managed_id: int,
        reason: str,
        *,
        log_event: str = "CUSTOM_RUNTIME_PREPARATION_FAILED",
    ) -> None:
        # Preserve deliberate financial/lifecycle skips and permanent terminal
        # states. Ordinary execution/provider/runtime errors reconnect instead.
        if _stake_policy_reason(reason):
            previous = _ORIGINAL_FAIL_HANDLER
            if previous is not None:
                previous(bot, int(managed_id), reason, log_event=log_event)
            return
        account = bot.repository.managed_account(int(managed_id)) or {}
        if not bool(account.get("enabled")):
            return
        _request_private_reconnect(bot, int(managed_id), reason)
        if any(
            marker in str(reason or "").lower()
            for marker in ("proposal", "public websocket", "market stream", "not connected")
        ):
            _request_public_reconnect(bot, reason)
        bot.logger.warning(
            "%s managed_id=%s action=reconnect_not_stop enabled_preserved=true reason=%s",
            log_event,
            int(managed_id),
            str(reason or "execution failure")[:140],
        )

    AccountExecutionSession.buy_proposal = buy_with_reconnect_not_stop  # type: ignore[method-assign]
    AccountExecutionSession.execute_real = execute_real_with_ambiguity_lock  # type: ignore[method-assign]
    RFDir5Repository.plan_stake = plan_stake_with_pure_multiplier  # type: ignore[method-assign]
    RFDir5Repository.record_account_outcome = record_outcome_with_immediate_virtual_state  # type: ignore[method-assign]
    direct_runtime._execute_for_account = execute_account_with_virtual_wakeup
    direct_runtime._account_has_open_actual = open_actual_including_ambiguity
    direct_runtime._fail_closed = reconnect_instead_of_execution_stop
    RFDir5TradingBot.handle_contract_update = handle_contract_with_virtual_wakeup  # type: ignore[method-assign]

    RFDir5TradingBot._custom_execution_consistency_authority_installed = True
    RFDir5TradingBot._custom_execution_timeout_policy = "reconnect_reconcile_never_stop"
    RFDir5TradingBot._custom_multiplier_policy = "previous_actual_stake_times_multiplier"
    RFDir5TradingBot._custom_split_policy = "exact_debt_divided_by_remaining_successful_parts"
    _INSTALLED = True
