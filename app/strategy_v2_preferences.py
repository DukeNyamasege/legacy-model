from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.models import RuntimePreference, utc_now

STRATEGY_KEY_PREFIX = "account_strategy:v1:"
STRATEGY_VERSION = "multi-strategy-v3"
DEFAULT_FAMILY = "system"
DEFAULT_SIDE = "system"

STRATEGY_CATALOG: dict[str, dict[str, Any]] = {
    "system": {
        "label": "System Strategy",
        "description": (
            "Default AIDR sequence: OVER 1 normal, OVER 3 first recovery, "
            "virtual OVER 4, then real OVER 4 recovery."
        ),
        "sides": {
            "system": {
                "label": "System",
                "contract_type": "SYSTEM",
                "normal_rule": "OVER 1",
                "recovery_rule": "OVER 3 → virtual OVER 4 → real OVER 4",
            }
        },
    },
    "digits": {
        "label": "Over / Under",
        "description": (
            "Choose Over or Under and one prediction barrier. The selected "
            "contract and barrier remain unchanged in normal, recovery and virtual modes."
        ),
        "prediction_required": True,
        "sides": {
            "over": {
                "label": "Over",
                "contract_type": "DIGITOVER",
                "prediction_min": 0,
                "prediction_max": 8,
                "prediction_default": 2,
                "normal_rule": "User-selected DIGITOVER barrier",
                "recovery_rule": "Same DIGITOVER barrier in every mode",
            },
            "under": {
                "label": "Under",
                "contract_type": "DIGITUNDER",
                "prediction_min": 1,
                "prediction_max": 9,
                "prediction_default": 7,
                "normal_rule": "User-selected DIGITUNDER barrier",
                "recovery_rule": "Same DIGITUNDER barrier in every mode",
            },
        },
    },
    "parity": {
        "label": "Even / Odd",
        "description": (
            "Choose one parity contract. The same contract is retained through "
            "normal, recovery and virtual protection."
        ),
        "sides": {
            "even": {
                "label": "Even",
                "contract_type": "DIGITEVEN",
                "normal_rule": "Final digit is even",
                "recovery_rule": "Same DIGITEVEN contract in every mode",
            },
            "odd": {
                "label": "Odd",
                "contract_type": "DIGITODD",
                "normal_rule": "Final digit is odd",
                "recovery_rule": "Same DIGITODD contract in every mode",
            },
        },
    },
    "direction": {
        "label": "Rise / Fall",
        "description": (
            "Choose one directional contract. The same CALL or PUT contract is "
            "retained through normal, recovery and virtual protection."
        ),
        "sides": {
            "rise": {
                "label": "Rise",
                "contract_type": "CALL",
                "normal_rule": "Exit spot above entry spot",
                "recovery_rule": "Same CALL contract in every mode",
            },
            "fall": {
                "label": "Fall",
                "contract_type": "PUT",
                "normal_rule": "Exit spot below entry spot",
                "recovery_rule": "Same PUT contract in every mode",
            },
        },
    },
    "custom": {
        "label": "Custom Strategy",
        "description": (
            "Build your own market pattern. Select one, several, or all supported "
            "markets; choose one exact contract; then require every configured "
            "recent-digit or tick-direction condition to match before execution."
        ),
        "sides": {
            "custom": {
                "label": "Custom Pattern",
                "contract_type": "CUSTOM",
                "normal_rule": "User-defined AND pattern on selected markets",
                "recovery_rule": (
                    "The same custom contract waits for the next matching pattern; "
                    "manual System, Multiplier, or Split Martingale may size recovery."
                ),
            }
        },
    },
}


@dataclass(frozen=True, slots=True)
class StrategySelectionV2:
    family: str
    side: str
    contract_type: str
    label: str
    prediction: int | None = None
    version: str = STRATEGY_VERSION

    def to_dict(self) -> dict[str, Any]:
        family_meta = STRATEGY_CATALOG[self.family]
        side_meta = family_meta["sides"][self.side]
        prediction_label = (
            f" {self.prediction}" if self.family == "digits" and self.prediction is not None else ""
        )
        return {
            "family": self.family,
            "side": self.side,
            "prediction": self.prediction,
            "contract_type": self.contract_type,
            "label": f"{self.label}{prediction_label}",
            "version": self.version,
            "family_label": family_meta["label"],
            "description": family_meta["description"],
            "normal_rule": (
                f"{self.contract_type} {self.prediction}"
                if self.family == "digits"
                else side_meta["normal_rule"]
            ),
            "recovery_rule": side_meta["recovery_rule"],
        }


def _key(managed_account_id: int) -> str:
    return f"{STRATEGY_KEY_PREFIX}{int(managed_account_id)}"


def normalize_strategy(
    family: Any,
    side: Any,
    prediction: Any = None,
) -> StrategySelectionV2:
    normalized_family = str(family or DEFAULT_FAMILY).strip().lower()
    normalized_side = str(side or DEFAULT_SIDE).strip().lower()
    family_meta = STRATEGY_CATALOG.get(normalized_family)
    if family_meta is None:
        raise ValueError(
            "Unsupported strategy family; choose system, digits, parity, direction, or custom"
        )
    side_meta = family_meta["sides"].get(normalized_side)
    if side_meta is None:
        allowed = ", ".join(sorted(family_meta["sides"]))
        raise ValueError(f"Unsupported {normalized_family} side; choose {allowed}")

    normalized_prediction: int | None = None
    if normalized_family == "digits":
        if prediction is None or str(prediction).strip() == "":
            normalized_prediction = int(side_meta["prediction_default"])
        else:
            try:
                normalized_prediction = int(str(prediction).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError("Prediction must be a whole digit") from exc
        minimum = int(side_meta["prediction_min"])
        maximum = int(side_meta["prediction_max"])
        if not minimum <= normalized_prediction <= maximum:
            raise ValueError(
                f"{side_meta['label']} prediction must be between {minimum} and {maximum}"
            )

    return StrategySelectionV2(
        family=normalized_family,
        side=normalized_side,
        prediction=normalized_prediction,
        contract_type=str(side_meta["contract_type"]),
        label=f"{family_meta['label']} · {side_meta['label']}",
    )


def default_strategy() -> StrategySelectionV2:
    return normalize_strategy(DEFAULT_FAMILY, DEFAULT_SIDE)


def strategy_catalog_payload() -> dict[str, Any]:
    return {
        "version": STRATEGY_VERSION,
        "default": default_strategy().to_dict(),
        "families": STRATEGY_CATALOG,
        "switching_rule": (
            "Stop AutoTrade and wait for open contracts to settle before changing strategy. "
            "Every strategy uses the shared account-level protection lifecycle."
        ),
    }


def _decode_payload(raw: str) -> StrategySelectionV2:
    if not raw:
        return default_strategy()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return default_strategy()
        version = str(payload.get("version") or "")
        family = str(payload.get("family") or "").strip().lower()
        side = str(payload.get("side") or "").strip().lower()
        prediction = payload.get("prediction")
        # The former default was stored as digits/over without a barrier. Treat
        # that legacy value as the newly explicit System Strategy.
        if version not in {STRATEGY_VERSION, "multi-strategy-v2"} and family == "digits" and side == "over" and prediction is None:
            return default_strategy()
        return normalize_strategy(family, side, prediction)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default_strategy()


def read_strategy(database: Any, managed_account_id: int) -> StrategySelectionV2:
    with database.session() as session:
        row = session.get(RuntimePreference, _key(managed_account_id))
        raw = str(row.preference_value or "") if row else ""
    return _decode_payload(raw)


def write_strategy(
    session: Any,
    managed_account_id: int,
    *,
    family: Any,
    side: Any,
    prediction: Any = None,
) -> StrategySelectionV2:
    selection = normalize_strategy(family, side, prediction)
    preference_key = _key(managed_account_id)
    row = session.get(RuntimePreference, preference_key)
    payload = json.dumps(
        {
            "family": selection.family,
            "side": selection.side,
            "prediction": selection.prediction,
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


def install_strategy_v2_preferences() -> None:
    """Make v3 preferences authoritative for API and worker compatibility layers."""
    import app.strategy_preferences as legacy

    legacy.STRATEGY_CATALOG = STRATEGY_CATALOG
    legacy.DEFAULT_FAMILY = DEFAULT_FAMILY
    legacy.DEFAULT_SIDE = DEFAULT_SIDE
    legacy.StrategySelection = StrategySelectionV2
    legacy.normalize_strategy = normalize_strategy
    legacy.default_strategy = default_strategy
    legacy.strategy_catalog_payload = strategy_catalog_payload
    legacy.read_strategy = read_strategy
    legacy.write_strategy = write_strategy
