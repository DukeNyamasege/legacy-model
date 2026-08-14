from __future__ import annotations

import hmac
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse

import app.api as base_api
from app.oauth_session_proof import validate_oauth_callback_proof


_INSTALLED = False


def _remove_routes(app: Any, paths: set[str]) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) not in paths
    ]


def install_oauth_session_recovery(app: Any) -> None:
    """Install the final OAuth callback and host-safe personal session cookie.

    The callback keeps one-time PKCE validation. It accepts the normal browser
    cookie proof or, when a browser/proxy drops those short-lived cookies, an
    equivalent proof made by the encrypted returned state and the one-time
    verifier stored in PostgreSQL. The final client session uses a host-only
    cookie, avoiding a stale or invalid configured Domain attribute.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    configured_cookie_domain = str(base_api.session_cookie_domain() or "").strip()
    configured_redirect_uri = str(base_api.oauth_redirect_url() or "").strip()
    original_oauth_start = base_api.oauth_start
    original_oauth_callback = base_api.oauth_callback

    # The OAuth callback lands on derivadmin.site itself. A host-only cookie is
    # valid for that host and also works for cross-origin API requests made to the
    # same API host with credentials enabled. It cannot be rejected because of a
    # stale www/non-www cookie-domain setting.
    base_api.session_cookie_domain = lambda: None

    _remove_routes(
        app,
        {"/oauth/start", "/oauth/callback", "/health/oauth-session"},
    )

    @app.get("/oauth/start")
    def resilient_oauth_start(request: Request) -> RedirectResponse:
        return original_oauth_start(request)

    @app.get("/oauth/callback")
    def resilient_oauth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ) -> RedirectResponse:
        if error:
            return base_api.redirect_with_oauth_error(error_description or error)
        if not code or not state:
            return base_api.redirect_with_oauth_error(
                "OAuth session is incomplete or expired"
            )

        stored_state = base_api.REPOSITORY.oauth_login_state(
            base_api.session_hash(state)
        )
        if not stored_state:
            return base_api.redirect_with_oauth_error(
                "OAuth session is incomplete, expired, or already used"
            )

        stored_redirect = str(stored_state.get("redirect_uri") or "").strip()
        if not stored_redirect or not hmac.compare_digest(
            stored_redirect,
            configured_redirect_uri,
        ):
            return base_api.redirect_with_oauth_error(
                "OAuth redirect URI validation failed"
            )

        try:
            stored_verifier = base_api.decrypt_token(
                stored_state["code_verifier_secret"],
                base_api.CONFIG.deriv.token_encryption_key,
            )
        except Exception:
            stored_verifier = ""

        cookie_state = str(
            request.cookies.get(base_api.OAUTH_STATE_COOKIE, "") or ""
        )
        cookie_verifier = str(
            request.cookies.get(base_api.OAUTH_VERIFIER_COOKIE, "") or ""
        )
        state_verifier = base_api.code_verifier_from_state(state)
        valid, proof_source = validate_oauth_callback_proof(
            returned_state=state,
            cookie_state=cookie_state,
            cookie_verifier=cookie_verifier,
            stored_verifier=stored_verifier,
            state_verifier=state_verifier,
        )
        if not valid:
            base_api.LOGGER.warning(
                "OAUTH_CALLBACK_PROOF_REJECTED host=%s cookie_state=%s "
                "cookie_verifier=%s stored_state=true",
                request.headers.get("host", "unknown"),
                bool(cookie_state),
                bool(cookie_verifier),
            )
            return base_api.redirect_with_oauth_error(
                "OAuth state or PKCE verification failed"
            )

        if proof_source == "server_state":
            base_api.LOGGER.warning(
                "OAUTH_CALLBACK_COOKIE_RECOVERED host=%s "
                "proof=encrypted_state_plus_database_pkce",
                request.headers.get("host", "unknown"),
            )

        response = original_oauth_callback(
            request,
            code=code,
            state=state,
            error="",
            error_description="",
            landed_redirect_uri=stored_redirect,
        )

        # Remove an older domain-scoped cookie so it cannot compete with the new
        # host-only value in the Cookie header after this successful login.
        stale_domains = {
            configured_cookie_domain,
            "derivadmin.site",
            ".derivadmin.site",
            "www.derivadmin.site",
        }
        for stale_domain in sorted(domain for domain in stale_domains if domain):
            response.delete_cookie(
                key=base_api.CLIENT_SESSION_COOKIE,
                path="/",
                domain=stale_domain,
            )
        # OAuth authorization codes and state are one-time callback material. Even
        # though the browser is immediately redirected to '/', never permit the
        # callback URL to be propagated as a Referer header.
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-OAuth-Session-Policy"] = (
            f"{proof_source};host-only-cookie;clean-url"
        )
        base_api.LOGGER.info(
            "OAUTH_SESSION_RESPONSE proof=%s status=%s host=%s "
            "cookie_domain=host-only",
            proof_source,
            response.status_code,
            request.headers.get("host", "unknown"),
        )
        return response

    @app.get("/health/oauth-session", include_in_schema=False)
    def oauth_session_health() -> dict[str, Any]:
        return {
            "status": "ready",
            "callback": "/oauth/callback",
            "client_session_cookie": "host-only",
            "browser_cookie_proof": True,
            "server_state_pkce_recovery": True,
            "callback_referrer_policy": "no-referrer",
        }

    app.state.oauth_session_recovery_installed = True
    _INSTALLED = True
