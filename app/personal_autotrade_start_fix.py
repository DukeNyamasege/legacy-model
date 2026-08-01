from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

import app.api as base_api
from app.services.telegram_api_alerts import queue_real_api_lifecycle_alert

_INSTALLED = False


def _token_scopes(payload: dict[str, Any]) -> set[str]:
    raw = str(
        payload.get("oauth_scope")
        or payload.get("scope")
        or payload.get("scopes")
        or ""
    ).replace(",", " ")
    return {item.strip().lower() for item in raw.split() if item.strip()}


def _account_purchase_token_from_payload(payload: dict[str, Any]) -> str:
    """Return the credential for this exact Demo/Real account only.

    A sibling Demo PAT must not make a Real account look executable.  For OAuth
    accounts, use the account's own OAuth access token only when it has the trade
    scope.  PAT accounts keep using their own stored PAT/access token.
    """

    explicit_pat = str(payload.get("pat_token", "")).strip()
    if explicit_pat:
        return explicit_pat

    auth_type = str(payload.get("auth_type", "pat")).strip().lower() or "pat"
    access_token = str(payload.get("access_token", "")).strip()
    if auth_type == "oauth":
        oauth_token = access_token or str(payload.get("oauth_access_token", "")).strip()
        return oauth_token if oauth_token and "trade" in _token_scopes(payload) else ""
    return access_token


def _has_account_purchase_token(payload: dict[str, Any]) -> bool:
    return bool(_account_purchase_token_from_payload(payload))


def _execution_requires_new_token(status: object) -> bool:
    """Only a confirmed rejected credential blocks Start.

    The old worker used `bulk_execution_pat_required` for OAuth accounts even
    after they had a trade-capable OAuth token.  That stale status made Start Auto
    Trade keep saying disabled.  Missing-token checks are now based on the actual
    current decrypted credential, not an old status label.
    """

    return str(status or "").strip().lower() == "credential_error"


def _install_api_token_semantics() -> None:
    # app.api.get_current_account resolves these globals at request time.  Rebinding
    # them fixes /me, /me/resume-trading, /me/auto-trade and the dashboard token
    # badges without editing the large base API file.
    base_api.trading_api_token_from_payload = _account_purchase_token_from_payload
    base_api.has_trading_api_token = _has_account_purchase_token
    base_api.has_personal_trading_api_token = _has_account_purchase_token
    base_api.execution_requires_new_token = _execution_requires_new_token

    def no_cross_mode_shared_token(_current_payload: dict[str, Any]) -> str:
        return ""

    base_api.shared_trading_api_token = no_cross_mode_shared_token


def _install_worker_token_semantics() -> None:
    try:
        from enhanced_bot import TradingBot
    except Exception:
        return

    def purchase_token_from_payload(self: TradingBot, payload: dict[str, Any]) -> str:
        del self
        return _account_purchase_token_from_payload(payload)

    TradingBot._purchase_token_from_payload = purchase_token_from_payload
    TradingBot._personal_account_token_semantics_installed = True


def _remove_route(path: str, method: str) -> None:
    method = method.upper()
    base_api.app.router.routes[:] = [
        route
        for route in base_api.app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in set(getattr(route, "methods", set()) or set())
        )
    ]


def _safe_audit(event: str, request: Request, account: dict[str, Any], payload: dict[str, Any]) -> None:
    try:
        base_api.REPOSITORY.audit(
            event,
            str(account.get("account_id_masked", "account")),
            request.client.host if request.client else "unknown",
            payload,
        )
    except Exception:
        base_api.LOGGER.exception("PERSONAL_AUTOTRADE_AUDIT_FAILED event=%s", event)


def _safe_lifecycle_alert(account_id: int, event: str, reason: str = "") -> None:
    try:
        queue_real_api_lifecycle_alert(
            base_api.REPOSITORY,
            base_api.CONFIG,
            base_api.LOGGER,
            managed_account_id=int(account_id),
            event=event,
            reason=reason,
        )
    except Exception:
        # Telegram/private admin notification must never make the trader's Start
        # button return HTTP 500 after the DB state was updated correctly.
        base_api.LOGGER.exception(
            "PERSONAL_AUTOTRADE_LIFECYCLE_ALERT_FAILED account_id=%s event=%s",
            account_id,
            event,
        )


def _start_account(
    request: Request,
    account: dict[str, Any],
    *,
    reset_recovery: bool,
) -> dict[str, Any]:
    if not account.get("has_trading_api_token", False):
        mode = str(account.get("account_type") or "account").upper()
        raise HTTPException(
            status_code=409,
            detail=(
                f"{mode} account is linked but does not have its own trade-capable "
                "credential. Save a Deriv token/OAuth login with trade scope for this "
                "same account mode, then press Start Auto Trade again."
            ),
        )

    account_id = int(account["id"])
    before_status = str(account.get("execution_status") or "inactive").strip().lower()
    try:
        if reset_recovery:
            base_api.REPOSITORY.resume_managed_account(account_id, reset_recovery=True)
        # Use direct row update, then status update, so older set_enabled wrappers
        # cannot turn this manual Start into a pause or a cross-mode state change.
        base_api.REPOSITORY.update_managed_account(account_id, enabled=True)
        base_api.REPOSITORY.set_managed_account_execution_status(
            account_id,
            "connecting",
            "Auto trading started manually for this account mode",
        )
        base_api.REPOSITORY.set_status("RUNNING", "")
        base_api.mark_dashboard_dirty(account.get("account_type"))
    except HTTPException:
        raise
    except Exception as exc:
        base_api.LOGGER.exception(
            "PERSONAL_AUTOTRADE_START_FAILED account_id=%s status_before=%s",
            account_id,
            before_status,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Auto trading start failed: {type(exc).__name__}",
        ) from exc

    event = "start" if reset_recovery or before_status in {"inactive", "disabled", "stopped", "real_disabled", "bulk_execution_pat_required", "token_required"} else "resume"
    _safe_lifecycle_alert(account_id, event)
    _safe_audit(
        "PERSONAL_AUTOTRADE_STARTED",
        request,
        account,
        {
            "managed_account_id": account_id,
            "account_type": account.get("account_type"),
            "reset_recovery": reset_recovery,
        },
    )
    return {
        "success": True,
        "enabled": True,
        "state": "running",
        "mode": "start_again" if reset_recovery else "continue",
        "managed_account_id": account_id,
    }


def _pause_account(request: Request, account: dict[str, Any]) -> dict[str, Any]:
    account_id = int(account["id"])
    reason = "Auto trading paused manually for this account mode"
    try:
        base_api.REPOSITORY.update_managed_account(account_id, enabled=False)
        base_api.REPOSITORY.set_managed_account_execution_status(
            account_id,
            "manual_pause",
            reason,
        )
        base_api.mark_dashboard_dirty(account.get("account_type"))
    except Exception as exc:
        base_api.LOGGER.exception("PERSONAL_AUTOTRADE_PAUSE_FAILED account_id=%s", account_id)
        raise HTTPException(
            status_code=500,
            detail=f"Auto trading pause failed: {type(exc).__name__}",
        ) from exc
    _safe_lifecycle_alert(account_id, "pause", reason=reason)
    _safe_audit(
        "PERSONAL_AUTOTRADE_PAUSED",
        request,
        account,
        {"managed_account_id": account_id, "account_type": account.get("account_type")},
    )
    return {"success": True, "enabled": False, "state": "paused"}


def install_personal_autotrade_start_fix() -> None:
    """Make Demo/Real Start button account-scoped and non-fragile.

    This fixes two production failures:
    * Start/Resume could return 500 because side effects such as lifecycle alerts
      were not isolated from the HTTP success path.
    * A Demo credential could be shared into the Real row for display/runtime
      readiness.  Each account mode now needs its own purchase credential or its
      own OAuth token with trade scope.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _install_api_token_semantics()
    _install_worker_token_semantics()
    _remove_route("/me/auto-trade", "POST")
    _remove_route("/me/resume-trading", "POST")

    @base_api.app.post("/me/auto-trade")
    def auto_trade_hardened(request: Request, body: base_api.AutoTradeRequest) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if body.enabled:
            return _start_account(request, account, reset_recovery=False)
        return _pause_account(request, account)

    @base_api.app.post("/me/resume-trading")
    def resume_trading_hardened(request: Request, body: base_api.ResumeTradeRequest) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return _start_account(
            request,
            account,
            reset_recovery=(str(body.mode) == "start_again"),
        )

    base_api.app.state.personal_autotrade_start_fix_installed = True
    _INSTALLED = True
