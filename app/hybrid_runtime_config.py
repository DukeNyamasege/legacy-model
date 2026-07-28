from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

import app.hybrid_digit_put as hybrid
from app.config import Test2Config
from app.models import Trade
from app.strategy.over2_strategy import TEST2_SYMBOLS


@dataclass(frozen=True, slots=True)
class HybridRuntimeConfig:
    enabled: bool = True
    version: str = "HYBRID-OVER2-PUT-RECOVERY-V4"
    primary_markets: tuple[str, ...] = ("1HZ100V",)
    recovery_markets: tuple[str, ...] = ("1HZ100V",)
    primary_contract_type: str = "DIGITOVER"
    primary_barrier: int = 2
    primary_pattern_ranges: tuple[tuple[int, int], ...] = (
        (6, 9),
        (6, 9),
        (0, 2),
        (0, 2),
        (3, 5),
    )
    over_barrier: int = 2
    under_barrier: int = 7
    duration_ticks: int = 1
    candidate_window_ms: int = 75

    # Production primary entry uses one recent-digit window plus the established
    # five-final-digit trigger. The previous 100/500/1000 gate is unused.
    recent_window: int = 20
    minimum_recent_hit_rate: float = 0.75
    minimum_bias_gap: float = 0.05
    minimum_live_edge: float = 0.02

    # Kept only so legacy V1 functions remain import-compatible. Production worker
    # replaces those functions with hybrid_recent_digit_bias before the bot starts.
    windows: tuple[int, int, int] = (100, 500, 1000)
    p100_edge: float = 0.04
    p500_edge: float = 0.02
    p1000_edge: float = 0.01
    confidence_z: float = 1.959963984540054


HYBRID_RUNTIME_CONFIG = HybridRuntimeConfig()
_INSTALLED = False


def install_hybrid_runtime_config() -> None:
    """Expose immutable hybrid parameters and prevent premature recovery exit.

    There is no daily loss cap or live shadow prerequisite at the global strategy
    layer. V3 recovery stake sizing is separately forced to each account's base
    stake by hybrid_safety; this module only owns shared mode/settlement sequencing.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    if not hasattr(Test2Config, "hybrid_strategy"):
        Test2Config.hybrid_strategy = property(  # type: ignore[attr-defined]
            lambda _self: HYBRID_RUNTIME_CONFIG
        )

    original_enter_recovery = hybrid._enter_recovery
    original_maybe_complete = hybrid._maybe_complete_recovery

    def enter_recovery_after_primary_settlement(bot, signal_id: str) -> None:
        original_enter_recovery(bot, signal_id)
        if hybrid._mode(bot) != hybrid.PUT_RECOVERY:
            return
        bot.hybrid_state["awaiting_participant_settlement"] = True
        hybrid._save_state(bot)
        bot.logger.warning(
            "HYBRID_RECOVERY_WAITING_FOR_ACCOUNT_SETTLEMENT signal_id=%s participants=%s",
            signal_id,
            len(bot.hybrid_state.get("participants", [])),
        )

    def complete_only_after_account_settlement(bot) -> None:
        if hybrid._mode(bot) != hybrid.PUT_RECOVERY:
            return

        enabled = hybrid._enabled_participants(bot)
        if not enabled:
            original_maybe_complete(bot)
            return

        if bool(bot.hybrid_state.get("awaiting_participant_settlement")):
            signal_id = str(bot.hybrid_state.get("primary_loss_signal") or "")
            if not signal_id:
                return
            with bot.repository.database.session() as session:
                rows = session.scalars(
                    select(Trade).where(
                        Trade.signal_id == signal_id,
                        Trade.managed_account_id.in_(sorted(enabled)),
                    )
                ).all()
            by_account = {
                int(row.managed_account_id): row
                for row in rows
                if row.managed_account_id is not None
            }
            if any(
                managed_id not in by_account
                or by_account[managed_id].settlement_time is None
                for managed_id in enabled
            ):
                return
            bot.hybrid_state["awaiting_participant_settlement"] = False
            hybrid._save_state(bot)
            bot.logger.info(
                "HYBRID_PRIMARY_ACCOUNT_SETTLEMENT_SYNCED signal_id=%s participants=%s",
                signal_id,
                len(enabled),
            )

        original_maybe_complete(bot)

    hybrid._enter_recovery = enter_recovery_after_primary_settlement
    hybrid._maybe_complete_recovery = complete_only_after_account_settlement
    _INSTALLED = True
