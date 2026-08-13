from __future__ import annotations

from typing import Any

from app import custom_strategy_direct_runtime as direct_runtime
from app.models import ManagedAccount, utc_now
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False
_CHAINED = False
_ORIGINAL_SCHEDULE: Any = None
_ORIGINAL_EVALUATE: Any = None
_ORIGINAL_BUILD: Any = None
_ORIGINAL_SET_STATUS: Any = None
_ACTIVE_BOT: RFDir5TradingBot | None = None


class _AccountStrategyFailure(RuntimeError):
    """Internal control-flow exception after an account is safely failed closed."""


def _managed_id_for_config(bot: RFDir5TradingBot, config: dict[str, Any]) -> int | None:
    for managed_id, item in list(getattr(bot, "_custom_direct_accounts", {}).items()):
        item_config = getattr(item, "config", None)
        if item_config is config or item_config == config:
            try:
                return int(managed_id)
            except (TypeError, ValueError):
                return None
    return None


def _error_reason(prefix: str, exc: Exception) -> str:
    detail = str(exc).strip()
    if not detail:
        detail = type(exc).__name__
    return f"Trading stopped: {prefix}: {detail}"[:160]


def _write_error(bot: RFDir5TradingBot, managed_id: int, reason: str, event: str) -> None:
    safe_reason = str(reason or "Trading stopped: Custom Strategy execution error")[:160]
    try:
        with bot.repository.database.session() as session:
            row = session.get(ManagedAccount, int(managed_id), with_for_update=True)
            if row is not None:
                row.enabled = False
                row.execution_status = "error"
                row.execution_status_reason = safe_reason
                row.execution_status_updated_at = utc_now()
                row.updated_at = utc_now()
    except Exception:
        bot.logger.exception("CUSTOM_STRATEGY_ERROR_STATUS_WRITE_FAILED managed_id=%s", managed_id)

    bot.valid_clients = [
        item
        for item in list(getattr(bot, "valid_clients", []) or [])
        if bot._managed_account_id_for_token(item[0]) != int(managed_id)
    ]
    getattr(bot, "_custom_direct_inflight", set()).discard(int(managed_id))
    bot.logger.error(
        "%s managed_id=%s reason=%s scanning=false purchase=false",
        event,
        managed_id,
        safe_reason,
    )


def install_custom_strategy_fail_visible() -> None:
    """Make invalid/broken Custom Strategy execution visible per account.

    A valid strategy that simply has not qualified remains WAITING. Only evaluator
    or signal-construction exceptions fail the affected account closed. The account
    row is persisted as ``error`` so /me/execution-runtime becomes ERROR and the
    dashboard's existing runtime notice can explain why no trade can be purchased.
    """

    global _INSTALLED, _ORIGINAL_SCHEDULE, _ORIGINAL_EVALUATE, _ORIGINAL_BUILD
    global _ORIGINAL_SET_STATUS
    if _INSTALLED:
        return

    _ORIGINAL_SCHEDULE = direct_runtime._schedule_account_matches
    _ORIGINAL_EVALUATE = direct_runtime.evaluate_custom_strategy
    _ORIGINAL_BUILD = direct_runtime.build_custom_signal
    _ORIGINAL_SET_STATUS = Test2Repository.set_managed_account_execution_status

    def evaluate_visible(config: dict[str, Any], *, digits: list[int], quotes: list[Any]) -> bool:
        try:
            return bool(_ORIGINAL_EVALUATE(config, digits=digits, quotes=quotes))
        except (TypeError, ValueError) as exc:
            reason = _error_reason("invalid Custom Strategy", exc)
            bot = _ACTIVE_BOT
            managed_id = _managed_id_for_config(bot, config) if bot is not None else None
            if bot is not None and managed_id is not None:
                _write_error(bot, managed_id, reason, "CUSTOM_STRATEGY_EVALUATION_FAILED")
            raise _AccountStrategyFailure(reason) from exc
        except Exception as exc:
            reason = _error_reason("strategy evaluation failed", exc)
            bot = _ACTIVE_BOT
            managed_id = _managed_id_for_config(bot, config) if bot is not None else None
            if bot is not None and managed_id is not None:
                _write_error(bot, managed_id, reason, "CUSTOM_STRATEGY_EVALUATION_FAILED")
            raise _AccountStrategyFailure(reason) from exc

    def build_visible(
        bot: RFDir5TradingBot,
        *,
        symbol: str,
        tick: dict[str, Any],
        config: dict[str, Any],
    ) -> Any:
        try:
            return _ORIGINAL_BUILD(bot, symbol=symbol, tick=tick, config=config)
        except _AccountStrategyFailure:
            raise
        except Exception as exc:
            managed_id = _managed_id_for_config(bot, config)
            reason = _error_reason("strategy signal could not be built", exc)
            if managed_id is not None:
                _write_error(
                    bot,
                    managed_id,
                    reason,
                    "CUSTOM_STRATEGY_SIGNAL_BUILD_FAILED",
                )
            raise _AccountStrategyFailure(reason) from exc

    def schedule_visible(
        bot: RFDir5TradingBot,
        *,
        symbol: str,
        tick: dict[str, Any],
    ) -> None:
        global _ACTIVE_BOT
        previous_bot = _ACTIVE_BOT
        _ACTIVE_BOT = bot
        try:
            _ORIGINAL_SCHEDULE(bot, symbol=symbol, tick=tick)
        except _AccountStrategyFailure:
            # The failing evaluator/build function has already persisted the exact
            # affected account as ERROR. Suppress only this tick's broken route so
            # unrelated public market processing remains alive.
            return
        finally:
            _ACTIVE_BOT = previous_bot

    def preserve_error_status(
        self: Test2Repository,
        account_id: int,
        execution_status: str,
        reason: str = "",
    ) -> None:
        requested = str(execution_status or "inactive").strip().lower()
        if requested == "error" or "error" in requested or "invalid" in requested or "failed" in requested:
            with self.database.session() as session:
                row = session.get(ManagedAccount, int(account_id), with_for_update=True)
                if row is not None:
                    row.enabled = False
                    row.execution_status = "error"
                    row.execution_status_reason = str(reason or "Execution failed safely.")[:160]
                    row.execution_status_updated_at = utc_now()
                    row.updated_at = utc_now()
                    return
        _ORIGINAL_SET_STATUS(self, int(account_id), execution_status, reason)

    direct_runtime.evaluate_custom_strategy = evaluate_visible
    direct_runtime.build_custom_signal = build_visible
    direct_runtime._schedule_account_matches = schedule_visible
    Test2Repository.set_managed_account_execution_status = preserve_error_status
    RFDir5TradingBot._custom_strategy_fail_visible_installed = True
    _INSTALLED = True


def chain_after_manual_stop_install() -> None:
    """Arrange installation after result routing and the manual-stop status guard."""

    global _CHAINED
    if _CHAINED:
        return
    from app import custom_strategy_manual_stop_guard as manual_stop

    original_install = manual_stop.install_custom_strategy_manual_stop_guard

    def install_manual_stop_with_visible_errors() -> None:
        original_install()
        install_custom_strategy_fail_visible()

    manual_stop.install_custom_strategy_manual_stop_guard = install_manual_stop_with_visible_errors
    _CHAINED = True
