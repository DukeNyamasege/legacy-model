from __future__ import annotations

import logging
from typing import Any

from enhanced_bot import TradingBot

_INSTALLED = False

_DIGIT_CONTRACTS = frozenset(
    {
        "DIGITOVER",
        "DIGITUNDER",
        "DIGITEVEN",
        "DIGITODD",
        "DIGITMATCH",
        "DIGITDIFF",
    }
)


def _settled_contract_duration(bot: TradingBot, contract: dict[str, Any]) -> int:
    """Return the contract duration represented by one settlement payload."""

    contract_type = str(contract.get("contract_type") or "").strip().upper()
    if contract_type in _DIGIT_CONTRACTS:
        # The proposal and private-buy boundaries hard-enforce one tick.
        return 1

    for key in ("duration", "duration_ticks", "tick_count"):
        try:
            value = int(contract.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value

    try:
        return max(1, int(getattr(bot, "duration", 1) or 1))
    except (TypeError, ValueError):
        return 1


class _SettlementObservabilityFilter(logging.Filter):
    """Correct legacy settlement records without changing trading behavior."""

    def __init__(self, bot: TradingBot) -> None:
        super().__init__()
        self.bot = bot

    def filter(self, record: logging.LogRecord) -> bool:
        message = str(record.msg or "")
        args = record.args

        if (
            message.startswith("CONTRACT_TIMING ")
            and isinstance(args, tuple)
            and len(args) >= 3
        ):
            values = list(args)
            contract_id = str(values[1])
            overrides = getattr(
                self.bot,
                "_settlement_duration_log_overrides",
                {},
            )
            duration = overrides.get(contract_id)
            if duration is not None:
                values[2] = int(duration)
                record.args = tuple(values)

        if (
            message.startswith("APP_MARKUP_NOT_CONFIRMED ")
            and isinstance(record.args, tuple)
            and len(record.args) >= 4
            and str(record.args[3] or "").strip().lower()
            in {"", "none", "unavailable"}
        ):
            # An omitted provider field cannot prove a configuration failure. Keep
            # the audit record, but describe it accurately and avoid alarming
            # warning noise for every successful settlement.
            record.msg = message.replace(
                "APP_MARKUP_NOT_CONFIRMED",
                "APP_MARKUP_UNVERIFIED",
                1,
            ).replace(
                "reported_app_markup_amount=%s;",
                "provider_markup_field=%s;",
                1,
            )
            record.levelno = logging.INFO
            record.levelname = "INFO"

        return True


def _ensure_filter(bot: TradingBot) -> None:
    if getattr(bot, "_settlement_observability_filter", None) is not None:
        return
    log_filter = _SettlementObservabilityFilter(bot)
    bot.logger.addFilter(log_filter)
    bot._settlement_observability_filter = log_filter


def install_settlement_observability_hardening() -> None:
    """Install the final settlement-log correction before bot startup."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_handle_contract_update = TradingBot.handle_contract_update

    async def handle_contract_update_with_exact_observability(
        self: TradingBot,
        token: str,
        contract_id: int,
        contract: dict[str, Any],
    ) -> None:
        _ensure_filter(self)
        overrides = getattr(self, "_settlement_duration_log_overrides", None)
        if not isinstance(overrides, dict):
            overrides = {}
            self._settlement_duration_log_overrides = overrides
        key = str(contract_id)
        overrides[key] = _settled_contract_duration(self, contract)
        try:
            await original_handle_contract_update(
                self,
                token,
                contract_id,
                contract,
            )
        finally:
            overrides.pop(key, None)

    TradingBot.handle_contract_update = (
        handle_contract_update_with_exact_observability
    )
    TradingBot._settlement_observability_hardening_installed = True
    _INSTALLED = True
