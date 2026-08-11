from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.models import RuntimePreference


PREFERENCE_PREFIX = "custom_strategy:v1:"
DEFAULT_VIRTUAL_ENTER_AFTER_LOSSES = 2
DEFAULT_VIRTUAL_EXIT_AFTER_CONSECUTIVE_WINS = 1


@dataclass(frozen=True)
class VirtualHookSettings:
    enabled: bool = True
    enter_after_losses: int = DEFAULT_VIRTUAL_ENTER_AFTER_LOSSES
    exit_after_consecutive_wins: int = DEFAULT_VIRTUAL_EXIT_AFTER_CONSECUTIVE_WINS

    @property
    def enter_after_runs(self) -> int:
        return self.enter_after_losses

    @property
    def exit_after_wins(self) -> int:
        return self.exit_after_consecutive_wins


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def normalize_virtual_hook_settings(raw: Any) -> VirtualHookSettings:
    source = raw if isinstance(raw, dict) else {}
    nested = source.get("virtual_hook") if isinstance(source.get("virtual_hook"), dict) else {}
    enabled = bool(source.get("virtual_hook_enabled", nested.get("enabled", True)))
    enter_after_losses = nested.get(
        "enter_after_losses",
        nested.get("enter_after_runs", source.get("virtual_hook_enter_after_losses")),
    )
    if enter_after_losses is None:
        enter_after_losses = source.get("virtual_hook_enter_after_runs")
    exit_after_consecutive_wins = nested.get(
        "exit_after_consecutive_wins",
        nested.get(
            "exit_after_wins",
            source.get("virtual_hook_exit_after_consecutive_wins"),
        ),
    )
    if exit_after_consecutive_wins is None:
        exit_after_consecutive_wins = source.get("virtual_hook_exit_after_wins")
    return VirtualHookSettings(
        enabled=enabled,
        enter_after_losses=_bounded_int(
            enter_after_losses,
            default=DEFAULT_VIRTUAL_ENTER_AFTER_LOSSES,
            minimum=1,
            maximum=50,
        ),
        exit_after_consecutive_wins=_bounded_int(
            exit_after_consecutive_wins,
            default=DEFAULT_VIRTUAL_EXIT_AFTER_CONSECUTIVE_WINS,
            minimum=1,
            maximum=50,
        ),
    )


def virtual_hook_settings_from_session(
    session: Any,
    managed_account_id: int,
) -> VirtualHookSettings:
    row = session.get(RuntimePreference, f"{PREFERENCE_PREFIX}{int(managed_account_id)}")
    if row is None:
        return VirtualHookSettings()
    try:
        payload = json.loads(str(row.preference_value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return VirtualHookSettings()
    return normalize_virtual_hook_settings(payload)
