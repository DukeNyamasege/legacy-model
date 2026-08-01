from __future__ import annotations

import copy
from typing import Any

import app.api as base_api
from app.token_store import decrypt_auth_payload

_INSTALLED = False
_ORIGINAL_DASHBOARD_SUMMARY = None
_ORIGINAL_BUILD_DASHBOARD_SNAPSHOT = None
_ORIGINAL_FILTER_SUMMARY = None

_STOPPED_OR_BLOCKED = {
    "inactive",
    "disabled",
    "stopped",
    "manual_pause",
    "take_profit",
    "stop_loss",
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "real_disabled",
    "duplicate",
    "purchase_registration_error",
}


def _row_context(row: Any) -> tuple[str, str, bool]:
    try:
        payload = decrypt_auth_payload(
            row.token_secret,
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception:
        return "", "", False
    account_id = str(payload.get("account_id") or "").strip()
    account_type = base_api.normalize_account_type(
        payload.get("account_type") or payload.get("environment")
    )
    credential_ready = bool(base_api.has_personal_trading_api_token(payload))
    return account_id, account_type, credential_ready


def live_participation_counts() -> dict[str, int]:
    active_ids: set[str] = set()
    active_demo_ids: set[str] = set()
    active_real_ids: set[str] = set()
    connected_ids: set[str] = set()

    for row in base_api.REPOSITORY.list_managed_accounts():
        account_id, account_type, credential_ready = _row_context(row)
        if not account_id or not credential_ready or not bool(row.enabled):
            continue
        status = str(row.execution_status or "inactive").strip().lower()
        if status in _STOPPED_OR_BLOCKED:
            continue
        active_ids.add(account_id)
        if account_type == "real":
            active_real_ids.add(account_id)
        else:
            active_demo_ids.add(account_id)
        if status in {
            "active",
            "recovery_pending",
            "virtual_protection",
            "base_stake_protection",
        }:
            connected_ids.add(account_id)

    return {
        "active_traders": len(active_ids),
        "trading_now": len(active_ids),
        "active_demo_traders": len(active_demo_ids),
        "active_real_traders": len(active_real_ids),
        "connected_traders": len(connected_ids),
        "registered_traders": int(base_api.REPOSITORY.managed_account_count()),
    }


def _apply_live_counts(payload: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    counts = live_participation_counts()
    result.update(counts)
    result["total_traders"] = counts["registered_traders"]
    result["active_trader_count_source"] = "live_managed_accounts"
    result["active_trader_definition"] = (
        "enabled + own trade-capable credential + not stopped/paused/fatally blocked"
    )
    return result


def install_live_trading_count() -> None:
    global _INSTALLED
    global _ORIGINAL_DASHBOARD_SUMMARY, _ORIGINAL_BUILD_DASHBOARD_SNAPSHOT
    global _ORIGINAL_FILTER_SUMMARY
    if _INSTALLED:
        return

    _ORIGINAL_DASHBOARD_SUMMARY = base_api.dashboard_summary
    _ORIGINAL_BUILD_DASHBOARD_SNAPSHOT = base_api._build_dashboard_snapshot
    _ORIGINAL_FILTER_SUMMARY = base_api.filter_summary_to_trading_ready_accounts

    def dashboard_summary_live(*, force: bool = False, account_type: str = "demo") -> dict:
        return _apply_live_counts(
            _ORIGINAL_DASHBOARD_SUMMARY(force=force, account_type=account_type)
        )

    def build_dashboard_snapshot_live(account_type: str):
        payload, generated_at, watermark = _ORIGINAL_BUILD_DASHBOARD_SNAPSHOT(account_type)
        payload = _apply_live_counts(payload)
        watermark = {
            **dict(watermark or {}),
            "live_active_traders": payload["active_traders"],
            "live_active_demo_traders": payload["active_demo_traders"],
            "live_active_real_traders": payload["active_real_traders"],
        }
        return payload, generated_at, watermark

    def filter_summary_live(summary: dict, *, account_type: str | None = None) -> dict:
        return _apply_live_counts(
            _ORIGINAL_FILTER_SUMMARY(summary, account_type=account_type)
        )

    base_api.dashboard_summary = dashboard_summary_live
    base_api._build_dashboard_snapshot = build_dashboard_snapshot_live
    base_api.filter_summary_to_trading_ready_accounts = filter_summary_live
    base_api.app.state.live_trading_count_installed = True
    _INSTALLED = True
