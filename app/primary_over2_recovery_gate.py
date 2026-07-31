from __future__ import annotations

import time
from typing import Any

import app.hybrid_digit_put as hybrid
from app.rf_dir5_bot import RFDir5TradingBot


_INSTALLED = False


def _recovery_accounts(bot: RFDir5TradingBot) -> set[int]:
    try:
        return set(hybrid._recovery_account_ids(bot))
    except Exception as exc:
        now = time.monotonic()
        last = float(getattr(bot, "_primary_over2_gate_error_log_at", 0.0) or 0.0)
        if now - last >= 60.0:
            setattr(bot, "_primary_over2_gate_error_log_at", now)
            bot.logger.warning(
                "PRIMARY_OVER2_RECOVERY_GATE_CHECK_FAILED error=%s "
                "fail_safe=block_put_until_account_state_is_readable",
                type(exc).__name__,
            )
        return set()


def _discard_put_queue_until_recovery(bot: RFDir5TradingBot) -> bool:
    """Return True when PUT recovery must remain blocked.

    Production starts from OVER 2. PUT/FALL is only a recovery engine after an
    enabled account has one actual OVER-2 loss. After a recovery PUT win, the
    account returns to OVER 2 and PUT is blocked again.
    """

    recovery = _recovery_accounts(bot)
    if recovery:
        return False

    queued = list(getattr(bot, "rf_candidate_queue", []) or [])
    if queued:
        bot.rf_candidate_queue.clear()
        try:
            hybrid._maybe_complete_recovery(bot)
        except Exception:
            pass
        now = time.monotonic()
        last = float(getattr(bot, "_primary_over2_gate_last_log_at", 0.0) or 0.0)
        if now - last >= 60.0:
            setattr(bot, "_primary_over2_gate_last_log_at", now)
            bot.logger.info(
                "PRIMARY_OVER2_ONLY active=true primary_contract=DIGITOVER barrier=2 "
                "put_recovery_gate=one_over2_loss_then_repeat_put_until_one_win "
                "recovery_accounts=0 suppressed_put_candidates=%s",
                len(queued),
            )
    return True


def install_primary_over2_recovery_gate() -> None:
    """Block PUT/FALL scheduling until at least one account needs recovery."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_schedule = RFDir5TradingBot._schedule_candidate_arbitration
    original_arbitrate = RFDir5TradingBot._arbitrate_candidates

    def gated_schedule(self: RFDir5TradingBot) -> None:
        if _discard_put_queue_until_recovery(self):
            return
        original_schedule(self)

    async def gated_arbitrate(self: RFDir5TradingBot) -> Any:
        if _discard_put_queue_until_recovery(self):
            return None
        return await original_arbitrate(self)

    RFDir5TradingBot._schedule_candidate_arbitration = gated_schedule
    RFDir5TradingBot._arbitrate_candidates = gated_arbitrate
    RFDir5TradingBot._primary_over2_recovery_gate_installed = True
    _INSTALLED = True
