from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONTRACT_PATH = Path(__file__).with_name("aidr_strategy_contract.json")


def load_aidr_strategy_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        raise RuntimeError("Unsupported AIDR strategy contract schema")
    for section in ("product", "execution", "quality"):
        if not isinstance(payload.get(section), dict):
            raise RuntimeError(f"AIDR strategy contract is missing section: {section}")
    execution = payload["execution"]
    if execution.get("contract_type") != "DIGITOVER":
        raise RuntimeError("AIDR strategy contract must remain DIGITOVER")
    if bool(execution.get("virtual_provider_purchase")):
        raise RuntimeError("Virtual AIDR observations cannot purchase provider contracts")
    return payload


AIDR_STRATEGY_CONTRACT = load_aidr_strategy_contract()
