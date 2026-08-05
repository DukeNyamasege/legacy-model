from __future__ import annotations

from typing import Any


def pat_required_message() -> str:
    return (
        "Link your Deriv Personal Access Token with trade scope in "
        "Settings > Credentials to enable bulk purchase trading."
    )


def credential_status_from_execution(
    execution_status: Any,
    execution_status_reason: Any = "",
    *,
    has_token: bool = False,
) -> dict[str, Any]:
    """Normalize legacy execution statuses into the dashboard credential badge.

    This keeps the existing encrypted token storage intact while exposing the
    exact PAT states required by the Deriv bulk-purchase flow.
    """

    status = str(execution_status or "").strip().lower()
    reason = str(execution_status_reason or "").strip()
    if status in {"active", "connecting", "validating"} and has_token:
        return {
            "connected": True,
            "status": "connected",
            "label": "Connected",
            "message": "Deriv PAT connected.",
        }
    if status in {"credential_error", "token_required", "bulk_execution_pat_required"}:
        lower = reason.lower()
        if "expired" in lower or "rejected" in lower or "invalid" in lower:
            normalized = "expired"
            message = (
                "Your Deriv token has expired or is invalid. Go to Settings > "
                "Credentials and add a new trade-scope token."
            )
        elif "does not match" in lower or "another credential" in lower:
            normalized = "account_mismatch"
            message = (
                "The token you added does not belong to this account. Add the "
                "correct Deriv trade-scope token."
            )
        else:
            normalized = "missing"
            message = pat_required_message()
        return {
            "connected": False,
            "status": normalized,
            "label": normalized.replace("_", " ").title(),
            "message": message,
        }
    if has_token:
        return {
            "connected": True,
            "status": "connected",
            "label": "Connected",
            "message": "Deriv PAT connected.",
        }
    return {
        "connected": False,
        "status": "missing",
        "label": "Missing",
        "message": pat_required_message(),
    }
