from __future__ import annotations

import logging
from http.cookies import SimpleCookie
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import Response

import app.api as base_api
from app.services import telegram_admin
from app.telegram_silence import telegram_notifications_suspended
from app.token_store import decrypt_auth_payload


LOGGER = logging.getLogger("legacy_model.vps_session_observability")
_INSTALLED = False


def _remove_and_capture_route(app: Any, path: str, method: str) -> Callable[..., Any]:
    expected = method.upper()
    endpoint: Callable[..., Any] | None = None
    kept = []
    for route in list(app.router.routes):
        methods = set(getattr(route, "methods", set()) or set())
        if getattr(route, "path", None) == path and expected in methods:
            endpoint = getattr(route, "endpoint", None)
            continue
        kept.append(route)
    app.router.routes[:] = kept
    if endpoint is None:
        raise RuntimeError(f"Required route not found: {expected} {path}")
    return endpoint


def _session_token_from_response(response: Response) -> str:
    """Extract only the new non-empty HttpOnly client session from Set-Cookie."""

    result = ""
    for key, value in list(getattr(response, "raw_headers", []) or []):
        if bytes(key).lower() != b"set-cookie":
            continue
        try:
            cookie = SimpleCookie()
            cookie.load(bytes(value).decode("latin-1"))
        except Exception:
            continue
        morsel = cookie.get(base_api.CLIENT_SESSION_COOKIE)
        if morsel is not None and str(morsel.value or "").strip():
            result = str(morsel.value).strip()
    return result


def _managed_context(managed_id: int) -> dict[str, Any] | None:
    row = base_api.REPOSITORY.managed_account(int(managed_id)) or {}
    secret = str(row.get("token_secret") or "")
    if not secret:
        return None
    try:
        payload = decrypt_auth_payload(
            secret,
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception:
        return None

    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        return None
    account_type = base_api.account_type_from_payload(payload)
    try:
        summary = base_api.REPOSITORY.account_summary(
            account_id,
            managed_account_id=int(managed_id),
        )
    except Exception:
        summary = {}
    return {
        "managed_id": int(managed_id),
        "account_id": account_id,
        "masked": base_api.mask_account_id(account_id),
        "account_type": account_type,
        "balance": float(summary.get("balance") or 0.0),
        "currency": str(summary.get("currency") or "USD"),
        "enabled": bool(row.get("enabled", False)),
        "status": str(row.get("execution_status") or "inactive"),
        "stake": float(row.get("stake_amount") or 0.0),
        "payload": payload,
    }


def _linked_login_contexts(selected_managed_id: int) -> list[dict[str, Any]]:
    selected = _managed_context(int(selected_managed_id))
    if selected is None:
        return []
    identity = base_api.login_identity_from_payload(selected["payload"])
    contexts: list[dict[str, Any]] = []
    seen: set[int] = set()

    for row in base_api.REPOSITORY.list_managed_accounts():
        managed_id = int(row.id)
        try:
            payload = decrypt_auth_payload(
                row.token_secret,
                base_api.CONFIG.deriv.token_encryption_key,
            )
        except Exception:
            continue
        if identity and base_api.login_identity_from_payload(payload) != identity:
            continue
        if not identity and managed_id != int(selected_managed_id):
            continue
        context = _managed_context(managed_id)
        if context is None or managed_id in seen:
            continue
        seen.add(managed_id)
        contexts.append(context)

    if int(selected_managed_id) not in seen:
        contexts.append(selected)

    contexts.sort(
        key=lambda item: (
            0 if item["account_type"] == "demo" else 1,
            item["account_id"],
        )
    )
    return contexts


def _queue_login_alert(selected_managed_id: int) -> None:
    def work() -> None:
        contexts = _linked_login_contexts(int(selected_managed_id))
        selected = next(
            (item for item in contexts if item["managed_id"] == int(selected_managed_id)),
            contexts[0] if contexts else None,
        )
        if selected is None:
            LOGGER.warning(
                "TELEGRAM_LOGIN_ALERT_SKIPPED managed_id=%s reason=account_context_missing",
                selected_managed_id,
            )
            return

        demo = [item for item in contexts if item["account_type"] == "demo"]
        real = [item for item in contexts if item["account_type"] == "real"]
        lines = [
            "🔐 DERIV USER LOGGED IN",
            "",
            f"Selected account: {selected['masked']} [{selected['account_type'].upper()}]",
            "Linked Options accounts:",
        ]
        for item in demo:
            lines.append(
                f"• DEMO {item['masked']} — {item['balance']:,.2f} {item['currency']}"
            )
        if not demo:
            lines.append("• DEMO/DOT — not returned for this login")
        for item in real:
            lines.append(
                f"• REAL {item['masked']} — {item['balance']:,.2f} {item['currency']}"
            )
        if not real:
            lines.append("• REAL/ROT — not returned for this login")
        lines.extend(
            (
                "",
                "Auto Trade: STOPPED until the trader explicitly presses Start.",
                f"Linked account count: {len(contexts)}",
            )
        )

        sent = telegram_admin._send_private_sync(
            base_api.REPOSITORY,
            base_api.CONFIG.telegram,
            LOGGER,
            "\n".join(lines),
        )
        LOGGER.info(
            "TELEGRAM_LOGIN_ALERT_%s selected=%s demo_accounts=%s real_accounts=%s",
            "SENT" if sent else "PENDING",
            selected["masked"],
            len(demo),
            len(real),
        )

    telegram_admin._queue(work)


def _queue_auto_trade_started(managed_id: int, *, fresh: bool) -> None:
    def work() -> None:
        context = _managed_context(int(managed_id))
        if context is None:
            LOGGER.warning(
                "TELEGRAM_AUTOTRADE_ALERT_SKIPPED managed_id=%s reason=account_context_missing",
                managed_id,
            )
            return
        text = "\n".join(
            (
                "🟢 AUTO TRADE STARTED",
                "",
                f"Account: {context['masked']}",
                f"Type: {context['account_type'].upper()}",
                f"Balance: {context['balance']:,.2f} {context['currency']}",
                f"Base stake: {context['stake']:.2f} {context['currency']}",
                f"Start mode: {'FRESH FROM ZERO' if fresh else 'RESUME'}",
                "Execution: private Deriv session is now being created for this account.",
            )
        )
        sent = telegram_admin._send_private_sync(
            base_api.REPOSITORY,
            base_api.CONFIG.telegram,
            LOGGER,
            text,
        )
        LOGGER.info(
            "TELEGRAM_AUTOTRADE_START_%s account=%s type=%s fresh=%s",
            "SENT" if sent else "PENDING",
            context["masked"],
            context["account_type"],
            str(bool(fresh)).lower(),
        )

    telegram_admin._queue(work)


def install_vps_session_observability(app: Any) -> None:
    """Observe final OAuth and Start routes after every compatibility installer."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_callback = _remove_and_capture_route(app, "/oauth/callback", "GET")
    original_auto_trade = _remove_and_capture_route(app, "/me/auto-trade", "POST")
    original_resume = _remove_and_capture_route(app, "/me/resume-trading", "POST")

    @app.get("/oauth/callback")
    def observed_oauth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ) -> Response:
        response = original_callback(
            request,
            code=code,
            state=state,
            error=error,
            error_description=error_description,
        )
        try:
            raw_session = _session_token_from_response(response)
            if raw_session:
                account = base_api.REPOSITORY.client_session_account(
                    base_api.session_hash(raw_session)
                )
                if account:
                    managed_id = int(account["id"])
                    base_api.LOGGER.info(
                        "FRESH_USER_LOGIN_SESSION_CREATED managed_id=%s account=%s",
                        managed_id,
                        account.get("account_id_masked", "unknown"),
                    )
                    _queue_login_alert(managed_id)
        except Exception:
            # Observability may never turn a successful Deriv OAuth callback into
            # a failed login.
            LOGGER.exception("TELEGRAM_LOGIN_OBSERVABILITY_FAILED")
        return response

    @app.post("/me/auto-trade")
    def observed_auto_trade(
        request: Request,
        body: base_api.AutoTradeRequest,
    ) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        result = original_auto_trade(request, body)
        if account and bool(body.enabled) and bool(result.get("success", True)):
            _queue_auto_trade_started(int(account["id"]), fresh=True)
        return result

    @app.post("/me/resume-trading")
    def observed_resume(
        request: Request,
        body: base_api.ResumeTradeRequest,
    ) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        result = original_resume(request, body)
        if account and bool(result.get("success", True)):
            fresh = bool(result.get("fresh_start")) or str(result.get("mode") or "") == "start_again"
            _queue_auto_trade_started(int(account["id"]), fresh=fresh)
        return result

    token_ready = bool(telegram_admin._bot_token(base_api.CONFIG.telegram))
    chat_ready = bool(telegram_admin._admin_chat_id(base_api.REPOSITORY))
    suspended = bool(telegram_notifications_suspended())
    LOGGER.warning(
        "VPS_SESSION_OBSERVABILITY_ACTIVE login_alerts=true autotrade_alerts=demo_and_real "
        "telegram_token_configured=%s telegram_admin_chat_configured=%s telegram_suspended=%s",
        str(token_ready).lower(),
        str(chat_ready).lower(),
        str(suspended).lower(),
    )

    app.state.vps_session_observability_installed = True
    _INSTALLED = True
