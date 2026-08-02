from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.api import DATABASE
from app.models import AccountRiskState, ManagedAccount, RuntimePreference, utc_now
from app.repositories.rf_dir5_repository import REAL_RECOVERY_PENDING, VIRTUAL_WAITING_FOR_WIN

BLOCKED_STATUSES = {
    "stopped",
    "disabled",
    "inactive",
    "manual_pause",
    "take_profit",
    "stop_loss",
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "insufficient_balance",
}


def _split_key(managed_account_id: int) -> str:
    return f"aidr_split_remaining:{int(managed_account_id)}"


def _split_remaining(session, managed_account_id: int) -> int:
    row = session.get(RuntimePreference, _split_key(managed_account_id))
    try:
        return max(0, min(2, int(str(row.preference_value if row else "0"))))
    except Exception:
        return 0


def _set_split(session, managed_account_id: int, value: int) -> None:
    key = _split_key(managed_account_id)
    row = session.get(RuntimePreference, key)
    if row is None:
        row = RuntimePreference(preference_key=key)
        session.add(row)
    row.preference_value = str(max(0, min(2, int(value))))
    row.updated_at = utc_now()


def _matches(state: AccountRiskState, row: ManagedAccount, suffix: str) -> bool:
    suffix = str(suffix or "").strip().upper()
    if not suffix:
        return False
    values = (
        str(state.account_id_masked or "").upper(),
        str(row.label or "").upper(),
    )
    return any(value.endswith(suffix) or suffix in value for value in values)


def repair_account(*, suffix: str, apply: bool) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with DATABASE.session() as session:
        states = session.scalars(select(AccountRiskState).order_by(AccountRiskState.managed_account_id)).all()
        for state in states:
            row = session.get(ManagedAccount, int(state.managed_account_id), with_for_update=apply)
            if row is None or not _matches(state, row, suffix):
                continue

            status = str(row.execution_status or "inactive").strip().lower()
            debt = round(float(state.recovery_loss_debt or 0.0), 2)
            split_remaining = _split_remaining(session, int(row.id))
            before = {
                "enabled": bool(row.enabled),
                "execution_status": status,
                "system_recovery_enabled": bool(getattr(row, "martingale_enabled", True)),
                "protection_mode": str(state.protection_mode or "NORMAL_MODE"),
                "consecutive_losses": int(state.consecutive_losses or 0),
                "recovery_loss_debt": debt,
                "recovery_pending": bool(state.recovery_pending),
                "recovery_attempt_active": bool(state.recovery_attempt_active),
                "virtual_wins": int(state.virtual_win_count or 0),
                "split_remaining": split_remaining,
            }

            action = "unchanged"
            reason = ""
            if status in BLOCKED_STATUSES:
                action = "skipped"
                reason = "Account is explicitly stopped/paused/quarantined; repair will not override user safety state."
            elif debt <= 0.009:
                action = "unchanged"
                reason = "No recovery debt exists."
            else:
                failed_recovery = bool(
                    state.protection_mode == VIRTUAL_WAITING_FOR_WIN
                    or (
                        split_remaining <= 0
                        and (
                            int(state.consecutive_losses or 0) >= 2
                            or bool(state.recovery_attempt_active)
                        )
                    )
                )
                if failed_recovery:
                    action = "virtual_protection"
                    reason = "Existing failed recovery moved to virtual OVER-3 confirmation."
                    if apply:
                        entering = state.protection_mode != VIRTUAL_WAITING_FOR_WIN
                        state.protection_mode = VIRTUAL_WAITING_FOR_WIN
                        state.recovery_pending = True
                        state.recovery_attempt_active = False
                        state.entered_virtual_mode_at = state.entered_virtual_mode_at or utc_now()
                        state.recovery_pending_since = state.recovery_pending_since or utc_now()
                        if entering:
                            state.virtual_observation_count = 0
                            state.virtual_win_count = 0
                            state.virtual_loss_count = 0
                            state.current_virtual_loss_streak = 0
                        _set_split(session, int(row.id), 0)
                        row.enabled = True
                        row.martingale_enabled = True
                        row.execution_status = "virtual_protection"
                        row.execution_status_reason = (
                            "AIDR state repaired: waiting for 2 consecutive virtual OVER-3 wins."
                        )
                else:
                    action = "recovery_pending"
                    reason = "Existing first-loss or split recovery debt kept in real OVER-3 recovery."
                    if apply:
                        state.protection_mode = REAL_RECOVERY_PENDING
                        state.recovery_pending = True
                        state.recovery_attempt_active = False
                        state.recovery_pending_since = state.recovery_pending_since or utc_now()
                        row.enabled = True
                        row.martingale_enabled = True
                        row.execution_status = "recovery_pending"
                        row.execution_status_reason = (
                            "AIDR state repaired: next qualifying entry is OVER-3 recovery."
                        )
                if apply:
                    state.updated_at = utc_now()
                    row.execution_status_updated_at = utc_now()
                    row.updated_at = utc_now()

            results.append(
                {
                    "managed_account_id": int(row.id),
                    "label": str(row.label or ""),
                    "account": str(state.account_id_masked or ""),
                    "action": action,
                    "reason": reason,
                    "before": before,
                    "applied": bool(apply and action not in {"skipped", "unchanged"}),
                }
            )

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "suffix": str(suffix),
        "mode": "apply" if apply else "dry-run",
        "matches": len(results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safely repair one AIDR account stuck after a failed recovery."
    )
    parser.add_argument("--account-suffix", required=True, help="Last account digits, e.g. 422")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the repair. Without this flag the command is read-only.",
    )
    args = parser.parse_args()
    print(json.dumps(repair_account(suffix=args.account_suffix, apply=args.apply), indent=2, default=str))


if __name__ == "__main__":
    main()
