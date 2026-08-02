from __future__ import annotations

from typing import Any

from app.token_store import decrypt_auth_payload

_INSTALLED = False


def _remove_route(app: Any, path: str, method: str) -> None:
    expected = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        )
    ]


def install_public_trader_stats_api(app: Any) -> None:
    """Expose stable public counts without requiring a login session.

    A trader with linked Demo and Real accounts is counted once when the OAuth
    identity can be resolved. PAT-only registrations fall back to their account
    identity. ``trading_now`` means at least one linked account is currently
    enabled for worker execution.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    import app.api as base_api

    _remove_route(app, "/metrics/public-traders", "GET")

    @app.get("/metrics/public-traders")
    def public_trader_stats() -> dict[str, Any]:
        registered: set[str] = set()
        active: set[str] = set()
        linked_accounts = 0
        enabled_accounts = 0

        for row in base_api.REPOSITORY.list_managed_accounts():
            linked_accounts += 1
            identity = ""
            try:
                payload = decrypt_auth_payload(
                    row.token_secret,
                    base_api.CONFIG.deriv.token_encryption_key,
                )
                identity = base_api.login_identity_from_payload(payload)
                if not identity:
                    account_id = str(payload.get("account_id") or "").strip()
                    identity = f"account:{account_id}" if account_id else ""
            except Exception:
                identity = ""
            identity = identity or f"managed:{int(row.id)}"
            registered.add(identity)

            status = str(row.execution_status or "inactive").strip().lower()
            currently_enabled = bool(row.enabled) and status not in {
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
            if currently_enabled:
                enabled_accounts += 1
                active.add(identity)

        return {
            "registered_traders": len(registered),
            "total_registered_traders": len(registered),
            "trading_now": len(active),
            "active_traders": len(active),
            "linked_accounts": linked_accounts,
            "enabled_accounts": enabled_accounts,
            "public": True,
        }

    app.state.public_trader_stats_api_installed = True
    _INSTALLED = True
