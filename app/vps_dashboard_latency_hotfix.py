from __future__ import annotations

from app.route_utils import remove_route as _remove_route

"""Full-VPS dashboard/session latency hotfix.

The production VPS keeps trading execution in the worker process. This module only
changes browser/API reads. It avoids the legacy session fallback and the global
managed-account identity scan on the hot `/me` path, which is especially important
after an administrator reset leaves historical ManagedAccount rows preserved.
"""

from typing import Any

from fastapi import Request

import app.api as base_api
import app.api_performance_hardening as performance
from app.token_store import decrypt_auth_payload


_INSTALLED = False




def _selected_session_account(request: Request) -> dict[str, Any] | None:
    """Resolve only the account selected by the durable browser session.

    This intentionally does not enumerate/decrypt every managed account. Linked
    Demo/Real discovery remains available on explicit account-switch operations;
    ordinary dashboard polling must remain O(1) with respect to historical users.
    """

    session_token = str(request.cookies.get(base_api.CLIENT_SESSION_COOKIE, "") or "")
    if not session_token:
        return performance._local_dev_current_account(request)

    row = performance._session_account_row(base_api.session_hash(session_token))
    if row is None:
        # The clean-reset architecture makes server-side ClientSession rows the
        # authority. A stale browser cookie must therefore become logged-out
        # immediately instead of entering the legacy all-account fallback path.
        return None

    try:
        payload = decrypt_auth_payload(
            row["token_secret"],
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception:
        return None

    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        return None

    account_type = base_api.account_type_from_payload(payload)
    rejected = base_api.execution_token_was_rejected(
        row["execution_status"],
        row["execution_status_reason"],
    )
    token_ready = bool(base_api.has_trading_api_token(payload)) and not bool(rejected)

    # Reuse already-warmed linked-account metadata if it exists, but never build
    # the expensive global identity index on a browser request.
    available_modes = [account_type]
    try:
        with performance._IDENTITY_LOCK:
            selected = dict(
                performance._IDENTITY_CACHE.get("by_managed_id", {}).get(int(row["id"]))
                or {}
            )
            identity = str(selected.get("identity") or "")
            linked = list(
                performance._IDENTITY_CACHE.get("by_identity", {}).get(identity) or []
            ) if identity else []
        if linked:
            available_modes = sorted(
                {str(item.get("account_type") or account_type) for item in linked},
                key=lambda value: {"demo": 0, "real": 1}.get(value, 9),
            )
            token_ready = any(bool(item.get("token_ready")) for item in linked) and not bool(rejected)
    except Exception:
        pass

    return {
        "id": int(row["id"]),
        "managed_account_id": int(row["id"]),
        "account_generation": f"{int(row['id'])}:{account_type}",
        "account_id": account_id,
        "account_id_masked": base_api.mask_account_id(account_id),
        "account_type": account_type,
        "available_account_types": available_modes or [account_type],
        "label": str(row["label"] or ""),
        "enabled": bool(row["enabled"]),
        "stake_amount": float(row["stake_amount"]),
        "take_profit": float(row["take_profit"]),
        "stop_loss": float(row["stop_loss"]),
        "martingale_enabled": bool(row["martingale_enabled"]),
        "execution_status": str(row["execution_status"]),
        "execution_status_reason": str(row["execution_status_reason"]),
        "has_trading_api_token": token_ready,
        "requires_api_token": not token_ready,
        "trading_api_token_invalid": bool(rejected),
        "created_at": row["created_at"],
    }


def install_vps_dashboard_latency_hotfix(app: Any) -> None:
    """Install constant-time session reads for the full VPS browser surface."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Existing performance-hardened route closures resolve this module global at
    # call time, so valid personal routes automatically benefit as well.
    performance._fast_current_account = _selected_session_account
    base_api.get_current_account = _selected_session_account

    # Replace `/me` explicitly so an invalid/reset cookie cannot fall through to
    # the pre-reset legacy session authority.
    _remove_route(app, "/me", "GET")

    @app.get("/me")
    def vps_personal_me(request: Request) -> dict[str, Any]:
        account = _selected_session_account(request)
        if not account:
            return {
                "authenticated": False,
                "performance_profile": "vps-constant-time-session-v1",
            }
        payload = performance._cached_me(account)
        payload["performance_profile"] = "vps-constant-time-session-v1"
        return payload

    app.state.vps_dashboard_latency_hotfix_installed = True
    app.state.vps_personal_session_profile = "constant-time-selected-account-v1"
    base_api.LOGGER.warning(
        "VPS_DASHBOARD_LATENCY_HOTFIX_ACTIVE selected_session_o1=true "
        "legacy_session_fallback=false trading_worker_untouched=true"
    )
    _INSTALLED = True
