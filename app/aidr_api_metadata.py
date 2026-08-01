from __future__ import annotations

from typing import Any

from fastapi import Depends

import app.api as base_api

AIDR_DISPLAY_NAME = "AI Digit Recovery V1"
AIDR_RUN_ID = "aidr_over1_over3_v1"
AIDR_PHASE = "AIDR_OVER1_OVER3_SPLIT"

_INSTALLED = False


def _remove_route(path: str, method: str) -> None:
    expected = method.upper()
    base_api.app.router.routes[:] = [
        route
        for route in base_api.app.router.routes
        if not (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        )
    ]


def _apply_aidr_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    payload.update(
        {
            "strategy_name": AIDR_DISPLAY_NAME,
            "execution_phase": AIDR_PHASE,
            "strategy_family": "DIGITOVER",
            "normal_contract_type": "DIGITOVER",
            "normal_barrier": 1,
            "recovery_contract_type": "DIGITOVER",
            "recovery_barrier": 3,
            "run_id": AIDR_RUN_ID,
        }
    )
    strategy = dict(payload.get("strategy") or {})
    strategy.update(
        {
            "name": AIDR_DISPLAY_NAME,
            "run_id": AIDR_RUN_ID,
            "normal": "DIGITOVER 1",
            "recovery": "DIGITOVER 3",
            "virtual_confirmation_wins": 2,
            "split_recovery_targets": 2,
        }
    )
    payload["strategy"] = strategy
    return payload


def install_aidr_api_metadata() -> None:
    """Keep the public API aligned with AIDR while config.yaml stays validator-safe.

    The legacy Pydantic config still requires the old literal values for a few
    fields.  AIDR is installed by code, so config.yaml must remain compatible for
    API import health, while dashboard/API metadata should show the active public
    strategy: OVER 1 normal and OVER 3 recovery.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route("/metrics/summary", "GET")
    _remove_route("/metrics/model", "GET")
    _remove_route("/metrics/rf-strategy", "GET")

    @base_api.app.get("/metrics/summary")
    def metrics_summary_aidr(mode: str = "demo") -> dict[str, Any]:
        account_type = base_api.normalize_account_type(mode)
        summary = base_api.dashboard_summary(account_type=account_type)
        summary.update({"dashboard_account_type": account_type})
        return _apply_aidr_metadata(summary)

    @base_api.app.get("/metrics/model")
    def model_metrics_aidr(_: str = Depends(base_api.require_control_auth)) -> dict[str, Any]:
        return {
            "strategy": {
                "name": AIDR_DISPLAY_NAME,
                "run_id": AIDR_RUN_ID,
                "phase": AIDR_PHASE,
                "normal_contract_type": "DIGITOVER",
                "normal_barrier": 1,
                "recovery_contract_type": "DIGITOVER",
                "recovery_barrier": 3,
                "martingale_enabled": base_api.CONFIG.risk.recovery_enabled,
                "recovery_trigger_losses": 1,
                "virtual_protection_enabled": True,
                "virtual_trigger_actual_losses": 2,
                "virtual_confirmation_wins": 2,
                "split_recovery_targets": 2,
            }
        }

    @base_api.app.get("/metrics/rf-strategy")
    def rf_strategy_metrics_aidr(_: str = Depends(base_api.require_control_auth)) -> dict[str, Any]:
        return {
            "strategy": AIDR_DISPLAY_NAME,
            "run_id": AIDR_RUN_ID,
            "phase": AIDR_PHASE,
            "contract_type": "DIGITOVER",
            "normal_barrier": 1,
            "recovery_barrier": 3,
            "markets": list(base_api.CONFIG.strategy.symbols),
            "duration_ticks": 1,
            "legacy_rf_infrastructure": "disabled_for_entry_selection",
        }

    base_api.app.state.aidr_api_metadata_installed = True
    _INSTALLED = True
