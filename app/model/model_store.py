from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.model.feature_builder import FEATURE_SCHEMA_VERSION


def persist_model_metadata(
    directory: str | Path,
    *,
    model_id: str,
    model_version: str,
    training_run_id: str,
    training_tick_range: tuple[int, int],
    observation_count: int,
    state_mappings: dict[str, Any],
    validation_metrics: dict[str, Any],
) -> dict[str, Any]:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    metadata = {
        "model_id": model_id,
        "model_version": model_version,
        "training_run_id": training_run_id,
        "training_tick_range": list(training_tick_range),
        "number_of_observations": observation_count,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "state_mappings": state_mappings,
        "validation_metrics": validation_metrics,
    }
    canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    metadata["checksum"] = hashlib.sha256(canonical).hexdigest()
    (target / f"{model_id}.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    return metadata
