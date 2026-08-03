from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select

import app.ai_digit_recovery_v1 as aidr
from app.ai_digit_recovery_v1 import VIRTUAL_WINS_REQUIRED, _write_split_remaining
from app.aidr_adaptive_virtual import adaptive_virtual_wins_required
from app.models import AccountRiskState, ManagedAccount, VirtualTrade
from app.repositories.rf_dir5_repository import NORMAL_MODE, REAL_RECOVERY_PENDING, VIRTUAL_WAITING_FOR_WIN

_INSTALLED = False


def _unwrap_legacy_feedback(
    settle: Callable[..., list[dict[str, Any]]],
) -> Callable[..., list[dict[str, Any]]]:
    """Remove only the legacy masked-account virtual feedback wrapper.

    That wrapper selected AccountRiskState by ``account_id_masked``. Its useful
    status feedback is replaced below using the exact VirtualTrade foreign key.
    """

    if getattr(settle, "__module__", "") != "app.account_execution_feedback":
        return settle
    closure = getattr(settle, "__closure__", None) or ()
    names = getattr(getattr(settle, "__code__", None), "co_freevars", ())
    values = {name: cell.cell_contents for name, cell in zip(names, closure)}
    original = values.get("original_settle_virtual_trades")
    return original if callable(original) else settle


def _safe_settle_factory(original_settle: Callable[..., list[dict[str, Any]]]):
    """Build the AIDR virtual wrapper using VirtualTrade.managed_account_id.

    Masked login IDs are display values and are not unique database keys. The old
    AIDR and feedback wrappers could both select a stale duplicate row. The
    feedback layer is unwrapped and replaced with exact-row status handling here.
    """

    settle_exact = _unwrap_legacy_feedback(original_settle)

    def wrapped(self, **kwargs: Any) -> list[dict[str, Any]]:
        settled = settle_exact(
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
                debt = float(state.recovery_loss_debt or 0.0) if state is not None else 0.0
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
                _write_split_remaining(self.base, managed_id, 1)
                if enabled and status not in {"stopped", "inactive", "disabled", "manual_pause"}:
                    self.base.set_managed_account_execution_status(
                        managed_id,
                        "recovery_pending",
                        (
                            "One virtual OVER-4 win confirmed recovery. Next real OVER-4 "
                            "recovery targets the full debt once."
                        ),
                    )
            elif mode == VIRTUAL_WAITING_FOR_WIN:
                required = adaptive_virtual_wins_required(
                    self.base,
                    managed_id,
                    default_wins=VIRTUAL_WINS_REQUIRED,
                    recovery_debt=debt,
                )
                if enabled and status not in {"stopped", "inactive", "disabled", "manual_pause"}:
                    self.base.set_managed_account_execution_status(
                        managed_id,
                        "virtual_protection",
                        (
                            f"Virtual OVER-4 confirmation active: consecutive wins "
                            f"{wins}/{required}."
                        ),
                    )
        return settled

    return wrapped


def install_aidr_virtual_settlement_fix() -> None:
    """Patch the AIDR settlement factory before strategy installation."""

    global _INSTALLED
    if _INSTALLED:
        return
    # A fresh API/worker process installs this before AIDR. Do not crash an import
    # if another compatibility process already installed the strategy; settlement
    # authority is required only in the worker and its install order is explicit.
    if getattr(aidr, "_INSTALLED", False):
        _INSTALLED = True
        return
    aidr._settle_virtual_aidr = _safe_settle_factory
    _INSTALLED = True
