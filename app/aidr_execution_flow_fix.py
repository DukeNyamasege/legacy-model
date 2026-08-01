from __future__ import annotations

from typing import Any

from app.repositories.rf_dir5_repository import (
    NORMAL_MODE,
    RECOVERY_PENDING,
    VIRTUAL_MODE,
)
from app.rf_dir5_bot import RFDir5TradingBot

_INSTALLED = False


class _AIDRVirtualProtection(dict):
    """Compatibility payload for the legacy RF purchase envelope.

    The shared purchase envelope still contains legacy PUT guards:
    - primary digit + RECOVERY_PENDING => skip
    - primary digit + VIRTUAL_MODE => skip before virtual observation can open

    AIDR recovery and virtual observations are both DIGITOVER 3, so the first
    mode check must not trip those old PUT guards. The second mode check should
    still see VIRTUAL_MODE so the existing virtual-trade recorder opens a $0
    observation instead of a real contract.
    """

    def __init__(self, payload: dict[str, Any], *, first_mode: str) -> None:
        super().__init__(payload)
        self._first_mode = first_mode
        self._mode_reads = 0

    def get(self, key: Any, default: Any = None) -> Any:  # noqa: D401
        if key == "mode":
            self._mode_reads += 1
            if self._mode_reads == 1:
                return self._first_mode
        return super().get(key, default)


def _is_aidr_digit_signal(signal: Any) -> bool:
    contract_type = str(getattr(signal, "contract_type", "") or "").upper()
    barrier = str(getattr(signal, "barrier", "") or "").strip()
    trigger = str(getattr(signal, "trigger_name", "") or "").upper()
    direction = str(getattr(signal, "direction", "") or "").upper()
    return (
        contract_type == "DIGITOVER"
        and barrier in {"1", "3"}
        and (trigger.startswith("AIDR-") or direction in {"OVER_1", "OVER_3"})
    )


def install_aidr_execution_flow_fix() -> None:
    """Allow AIDR DIGITOVER 3 to perform recovery and virtual confirmation.

    The active AIDR model intentionally removed PUT. Older RF-DIR5 guards still
    assumed any DIGITOVER signal was a primary entry and any recovery had to be a
    PUT. That made accounts appear to stop after a loss because the next OVER 3
    recovery or virtual OVER 3 observation was skipped before stake planning.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_buy_selected = RFDir5TradingBot._buy_selected_accounts

    async def buy_selected_with_aidr_modes(
        self: RFDir5TradingBot,
        signal: Any,
        economics: Any,
    ) -> None:
        if not _is_aidr_digit_signal(signal):
            return await original_buy_selected(self, signal, economics)

        repository = getattr(self, "rf_repository", None)
        if repository is None:
            return await original_buy_selected(self, signal, economics)

        original_protection = repository.virtual_protection_for_account

        def aidr_virtual_protection_for_account(*args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = dict(original_protection(*args, **kwargs) or {})
            mode = str(payload.get("mode") or NORMAL_MODE)
            if mode == RECOVERY_PENDING:
                # Let the real DIGITOVER 3 recovery reach plan_stake(). The
                # database state remains REAL_RECOVERY_PENDING, so plan_stake()
                # still sizes the stake from the actual debt.
                patched = dict(payload)
                patched["mode"] = NORMAL_MODE
                patched["aidr_mode"] = RECOVERY_PENDING
                patched["next_action"] = "Next AIDR entry is real DIGITOVER 3 recovery"
                return patched
            if mode == VIRTUAL_MODE:
                # First get('mode') bypasses the obsolete primary-digit skip;
                # second get('mode') opens the existing virtual observation path.
                patched = dict(payload)
                patched["aidr_mode"] = VIRTUAL_MODE
                patched["next_action"] = "Waiting for AIDR virtual DIGITOVER 3 confirmation"
                return _AIDRVirtualProtection(patched, first_mode=NORMAL_MODE)
            return payload

        repository.virtual_protection_for_account = aidr_virtual_protection_for_account
        try:
            return await original_buy_selected(self, signal, economics)
        finally:
            repository.virtual_protection_for_account = original_protection

    RFDir5TradingBot._buy_selected_accounts = buy_selected_with_aidr_modes
    RFDir5TradingBot._aidr_execution_flow_fix_installed = True
    _INSTALLED = True
