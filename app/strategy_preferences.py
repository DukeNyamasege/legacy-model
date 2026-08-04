from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.models import ManagedAccount, RuntimePreference, utc_now

STRATEGY_KEY_PREFIX = "account_strategy:v1:"
DEFAULT_FAMILY = "digits"
DEFAULT_SIDE = "over"

STRATEGY_CATALOG: dict[str, dict[str, Any]] = {
    "digits": {
        "label": "Over / Under",
        "description": "One-tick final-digit contracts using the AIDR recovery framework.",
        "sides": {
            "over": {
                "label": "Over",
                "contract_type": "DIGITOVER",
                "normal_rule": "OVER 1",
                "recovery_rule": "OVER 3 / OVER 4",
            },
            "under": {
                "label": "Under",
                "contract_type": "DIGITUNDER",
                "normal_rule": "UNDER 8",
                "recovery_rule": "UNDER 6 / UNDER 5",
            },
        },
    },
    "parity": {
        "label": "Even / Odd",
        "description": "One-tick digit-parity contracts with account-level virtual protection.",
        "sides": {
            "even": {
                "label": "Even",
                "contract_type": "DIGITEVEN",
                "normal_rule": "Final digit is even",
                "recovery_rule": "Same EVEN contract with live proposal sizing",
            },
            "odd": {
                "label": "Odd",
                "contract_type": "DIGITODD",
                "normal_rule": "Final digit is odd",
                "recovery_rule": "Same ODD contract with live proposal sizing",
            },
        },
    },
    "direction": {
        "label": "Rise / Fall",
        "description": "Directional contracts driven by the existing RF-DIR5 movement model.",
        "sides": {
            "rise": {
                "label": "Rise",
                "contract_type": "CALL",
                "normal_rule": "Exit spot above entry spot",
                "recovery_rule": "Same CALL family with live proposal sizing",
            },
            "fall": {
                "label": "Fall",
                "contract_type": "PUT",
                "normal_rule": "Exit spot below entry spot",
                "recovery_rule": "Same PUT family with live proposal sizing",
            },
        },
    },
}


@dataclass(frozen=True, slots=True)
class StrategySelection:
    family: str
    side: str
    contract_type: str
    label: str
    version: str = "multi-strategy-v1"

    def to_dict(self) -> dict[str, Any]:
        family_meta = STRATEGY_CATALOG[self.family]
        side_meta = family_meta["sides"][self.side]
        return {
            "family": self.family,
            "side": self.side,
            "contract_type": self.contract_type,
            "label": self.label,
            "version": self.version,
            "family_label": family_meta["label"],
            "description": family_meta["description"],
            "normal_rule": side_meta["normal_rule"],
            "recovery_rule": side_meta["recovery_rule"],
        }


def _key(managed_account_id: int) -> str:
    return f"{STRATEGY_KEY_PREFIX}{int(managed_account_id)}"


def normalize_strategy(family: Any, side: Any) -> StrategySelection:
    normalized_family = str(family or DEFAULT_FAMILY).strip().lower()
    normalized_side = str(side or DEFAULT_SIDE).strip().lower()
    family_meta = STRATEGY_CATALOG.get(normalized_family)
    if family_meta is None:
        raise ValueError(
            f"Unsupported strategy family {family!r}; choose digits, parity, or direction"
        )
    side_meta = family_meta["sides"].get(normalized_side)
    if side_meta is None:
        allowed = ", ".join(sorted(family_meta["sides"]))
        raise ValueError(
            f"Unsupported {normalized_family} side {side!r}; choose {allowed}"
        )
    return StrategySelection(
        family=normalized_family,
        side=normalized_side,
        contract_type=str(side_meta["contract_type"]),
        label=f"{family_meta['label']} · {side_meta['label']}",
    )


def default_strategy() -> StrategySelection:
    return normalize_strategy(DEFAULT_FAMILY, DEFAULT_SIDE)


def strategy_catalog_payload() -> dict[str, Any]:
    return {
        "version": "multi-strategy-v1",
        "default": default_strategy().to_dict(),
        "families": STRATEGY_CATALOG,
        "switching_rule": (
            "Stop AutoTrade and wait for open contracts to settle before changing strategy. "
            "Trade history is retained and recovery state restarts from base stake."
        ),
    }


def read_strategy(database: Any, managed_account_id: int) -> StrategySelection:
    with database.session() as session:
        row = session.get(RuntimePreference, _key(managed_account_id))
        raw = str(row.preference_value or "") if row else ""
    if not raw:
        return default_strategy()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("strategy preference must be an object")
        return normalize_strategy(payload.get("family"), payload.get("side"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default_strategy()


def write_strategy(
    session: Any,
    managed_account_id: int,
    *,
    family: Any,
    side: Any,
) -> StrategySelection:
    selection = normalize_strategy(family, side)
    preference_key = _key(managed_account_id)
    row = session.get(RuntimePreference, preference_key)
    payload = json.dumps(
        {
            "family": selection.family,
            "side": selection.side,
            "version": selection.version,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    if row is None:
        session.add(
            RuntimePreference(
                preference_key=preference_key,
                preference_value=payload,
            )
        )
    else:
        row.preference_value = payload
        row.updated_at = utc_now()
    return selection


def managed_account_strategy(repository: Any, managed_account_id: int) -> StrategySelection:
    return read_strategy(repository.database, int(managed_account_id))


def strategy_for_token(bot: Any, token: str) -> StrategySelection:
    managed_id = bot._managed_account_id_for_token(token)
    if managed_id is None:
        return default_strategy()
    return managed_account_strategy(bot.repository, int(managed_id))


def selected_managed_ids(
    repository: Any,
    *,
    family: str,
    side: str,
    enabled_only: bool = True,
) -> set[int]:
    wanted = normalize_strategy(family, side)
    with repository.database.session() as session:
        query = select(ManagedAccount)
        if enabled_only:
            query = query.where(ManagedAccount.enabled.is_(True))
        rows = session.scalars(query).all()
    return {
        int(row.id)
        for row in rows
        if managed_account_strategy(repository, int(row.id)) == wanted
    }
