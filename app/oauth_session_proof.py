from __future__ import annotations

import hmac


def validate_oauth_callback_proof(
    *,
    returned_state: str,
    cookie_state: str,
    cookie_verifier: str,
    stored_verifier: str,
    state_verifier: str,
) -> tuple[bool, str]:
    """Validate one OAuth callback without weakening one-time PKCE protection.

    Browsers normally return both short-lived OAuth cookies. Some reverse-proxy,
    cookie-domain, or browser-policy combinations can omit them after the external
    authorization redirect. The encrypted state also carries the verifier and the
    same verifier is stored server-side in PostgreSQL. Matching those independent
    values is a safe recovery proof when the browser cookie pair is unavailable.
    """

    state = str(returned_state or "")
    stored = str(stored_verifier or "")
    browser_state = str(cookie_state or "")
    browser_verifier = str(cookie_verifier or "")
    encrypted_state_verifier = str(state_verifier or "")

    browser_cookie_proof = bool(
        state
        and stored
        and browser_state
        and browser_verifier
        and hmac.compare_digest(browser_state, state)
        and hmac.compare_digest(browser_verifier, stored)
    )
    if browser_cookie_proof:
        return True, "browser_cookie"

    server_state_proof = bool(
        state
        and stored
        and encrypted_state_verifier
        and hmac.compare_digest(encrypted_state_verifier, stored)
    )
    if server_state_proof:
        return True, "server_state"

    return False, "invalid"
