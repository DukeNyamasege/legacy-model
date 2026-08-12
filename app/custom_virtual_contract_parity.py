from __future__ import annotations

from decimal import Decimal
from typing import Any

from app import custom_strategy_v1 as custom
from app.repositories import rf_dir5_repository as rf_repo


_INSTALLED = False


def virtual_contract_display(
    contract_type: str,
    *,
    barrier: str | int | None = "",
    direction: str = "",
) -> str:
    """Return the exact Custom Strategy contract represented by a virtual row."""

    contract = str(contract_type or "").strip().upper()
    direction_value = str(direction or "").strip().upper()
    barrier_value = str(barrier or "").strip()
    labels = {
        "DIGITEVEN": "DIGITEVEN",
        "DIGITODD": "DIGITODD",
        "DIGITMATCH": "DIGITMATCH",
        "DIGITDIFF": "DIGITDIFF",
        "DIGITOVER": "DIGITOVER",
        "DIGITUNDER": "DIGITUNDER",
        "CALL": "CALL",
        "PUT": "PUT",
    }
    label = labels.get(contract, contract or direction_value or "CUSTOM")
    if contract in {"DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF"} and barrier_value:
        return f"{label} {barrier_value}"
    return label


def _exact_virtual_outcome(
    *,
    direction: str,
    contract_type: str,
    barrier: str | int | None,
    prediction_digit: int | None,
    entry_quote: Decimal,
    exit_quote: Decimal,
    exit_digit: int | None = None,
) -> tuple[str, int | None]:
    """Settle the hypothetical contract using the exact saved contract family."""

    contract = str(contract_type or "").strip().upper()
    normalized_direction = str(direction or "").strip().upper()
    digit = (
        int(exit_digit)
        if exit_digit is not None and 0 <= int(exit_digit) <= 9
        else rf_repo._final_digit_from_quote(exit_quote)
    )

    if contract == "DIGITEVEN":
        return ("WIN" if digit % 2 == 0 else "LOSS", digit)
    if contract == "DIGITODD":
        return ("WIN" if digit % 2 == 1 else "LOSS", digit)

    if contract in {"DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF"}:
        target = rf_repo._prediction_digit(
            direction=normalized_direction,
            barrier=barrier,
            prediction_digit=prediction_digit,
        )
        if target is None:
            raise ValueError(
                "Digit virtual trade is missing the saved Custom Strategy prediction"
            )
        if contract == "DIGITOVER":
            return ("WIN" if digit > target else "LOSS", digit)
        if contract == "DIGITUNDER":
            return ("WIN" if digit < target else "LOSS", digit)
        if contract == "DIGITMATCH":
            return ("WIN" if digit == target else "LOSS", digit)
        return ("WIN" if digit != target else "LOSS", digit)

    if contract == "CALL" or normalized_direction in {"RISE", "RISING"}:
        return ("WIN" if Decimal(exit_quote) > Decimal(entry_quote) else "LOSS", digit)
    if contract == "PUT" or normalized_direction in {"FALL", "FALLING"}:
        return ("WIN" if Decimal(exit_quote) < Decimal(entry_quote) else "LOSS", digit)

    # Keep compatibility for any historical custom direction not covered above.
    return rf_repo._virtual_trade_outcome_original(
        direction=direction,
        contract_type=contract_type,
        barrier=barrier,
        prediction_digit=prediction_digit,
        entry_quote=entry_quote,
        exit_quote=exit_quote,
        exit_digit=exit_digit,
    )


def install_custom_virtual_contract_parity() -> None:
    """Make the Virtual Hook use exactly what the account is actually trading."""

    global _INSTALLED
    if _INSTALLED:
        return

    if not hasattr(rf_repo, "_virtual_trade_outcome_original"):
        rf_repo._virtual_trade_outcome_original = rf_repo._virtual_trade_outcome
    original_protection_payload = rf_repo.RFDir5Repository._protection_payload

    def protection_payload(self: Any, state: Any) -> dict[str, Any]:
        payload = original_protection_payload(self, state)
        if state is None or str(payload.get("mode") or "") != rf_repo.VIRTUAL_MODE:
            return payload
        try:
            config = custom.read_custom_strategy(self.database, int(state.managed_account_id))
            contract_type, direction, barrier = custom.contract_for_config(config)
            label = virtual_contract_display(
                contract_type,
                barrier=barrier,
                direction=direction,
            )
        except Exception:
            label = "CUSTOM STRATEGY"
        required = max(1, int(payload.get("virtual_wins_required") or 1))
        payload["next_action"] = (
            f"Waiting for {required} virtual {label} win"
            f"{'' if required == 1 else 's'}"
        )
        return payload

    rf_repo._virtual_trade_outcome = _exact_virtual_outcome
    rf_repo.RFDir5Repository._protection_payload = protection_payload
    _INSTALLED = True
