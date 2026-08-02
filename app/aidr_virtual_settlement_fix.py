from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select

import app.ai_digit_recovery_v1 as aidr
from app.ai_digit_recovery_v1 import VIRTUAL_WINS_REQUIRED, _write_split_remaining
from app.models import AccountRiskState, ManagedAccount, VirtualTrade
from app.repositories.rf_dir5_repository import NORMAL_MODE, REAL_RECOVERY_PENDING, VIRTUAL_WAITING_FOR_WIN

_INSTALLED = False


def _safe_settle_factory(original_settle: Callable[..., list[dict[str, Any]]]):
    """Build the AIDR virtual wrapper using VirtualTrade.managed_account_id.

    Masked login IDs are display values and are not unique database keys. The old
    wrapper could select a stale duplicate row when the same Deriv account had been
    enrolled more than once.
    """

    def wrapped(self, **kwargs: Any) -> list[dict[str, Any]]:
        settled = original_settle(
            self,
            **{**kwargs, "exit_after_wins": VIRTUAL_WINS_REQUIRED},
        )
        for item in settled:
            virtual_trade_id = str(item.get("virtual_trade_id") or "").strip()
            if not virtual_trade_id:
                continue
            with self.database.session() as session:
                trade = session.scalar(
                    select(VirtualTrade).where(
                        VirtualTrade.virtual_trade_id == virtual_trade_id
                    )
                )
                if trade is None:
                    continue
                managed_id = int(trade.managed_account_id)
                state = session.get(AccountRiskState, managed_id)
                account = session.get(ManagedAccount, managed_id)
                mode = state.protection_mode if state is not None else NORMAL_MODE
                wins = int(state.virtual_win_count or 0) if state is not None else 0
                enabled = bool(account.enabled) if account is not None else False
                status = (
                    str(account.execution_status or "inactive").strip().lower()
                    if account is not None
                    else "missing"
                )

            # Status updates are informational only. Repository lifecycle guards
            # reject promotion of stopped/paused rows, and the final strict guard
            # performs a second race-safe lifecycle check.
            if mode == REAL_RECOVERY_PENDING:
                _write_split_remaining(self.base, managed_id, 2)
                if enabled and status not in {"stopped", "inactive", "disabled", "manual_pause"}:
                    self.base.set_managed_account_execution_status(
                        managed_id,
                        "recovery_pending",
                        (
                            "2 consecutive virtual OVER-3 wins confirmed. Next real OVER-3 "
                            "recovery will recover debt in 2 profit targets."
                        ),
                    )
            elif mode == VIRTUAL_WAITING_FOR_WIN:
                if enabled and status not in {"stopped", "inactive", "disabled", "manual_pause"}:
                    self.base.set_managed_account_execution_status(
                        managed_id,
                        "virtual_protection",
                        (
                            f"Virtual OVER-3 confirmation active: consecutive wins "
                            f"{wins}/{VIRTUAL_WINS_REQUIRED}."
                        ),
                    )
        return settled

    return wrapped


def install_aidr_virtual_settlement_fix() -> None:
    """Patch the AIDR settlement factory before strategy installation."""

    global _INSTALLED
    if _INSTALLED:
        return
    if getattr(aidr, "_INSTALLED", False):
        raise RuntimeError(
            "AIDR virtual settlement fix must be installed before the AIDR strategy"
        )
    aidr._settle_virtual_aidr = _safe_settle_factory
    _INSTALLED = True
