from __future__ import annotations

"""Give browser-direct transport traffic its own bounded security quota.

The generic personal-mutation limiter is intentionally conservative because it
protects user account changes. Browser ownership heartbeat/checkpoint traffic is
not an account-setting mutation: dropping either request can expire the browser
lease and incorrectly hand execution to the VPS. OTP bootstrap is also transport
setup and must not consume the same 30/min bucket as Stop/Start/settings.

Origin/CSRF checks remain in the existing request-security middleware. This
module replaces only the per-session rate bucket selected by that middleware.
"""

import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import HTTPException

import app.api as base_api


_INSTALLED = False
_RUNTIME_RATE: dict[str, dict[str, deque[float]]] = defaultdict(
    lambda: defaultdict(deque)
)
_RUNTIME_RATE_LOCK = threading.Lock()

# Normal browser cadence is far below these values. They provide abuse bounds
# without allowing transport liveness to consume the ordinary 30/min mutation
# budget. Stop/Arm and every other account-changing route keep the old limiter.
_RUNTIME_LIMITS: dict[str, int] = {
    "/me/direct-execution/heartbeat": 240,
    "/me/direct-execution/checkpoint": 240,
    "/me/direct-execution/session": 60,
}


def _rate_key(request: Any) -> str:
    session_token = request.cookies.get(base_api.CLIENT_SESSION_COOKIE, "")
    client = request.client.host if request.client else "unknown"
    return base_api.session_hash(session_token) if session_token else f"ip:{client}"


def _enforce_direct_runtime_rate(request: Any, path: str, limit: int) -> None:
    key = _rate_key(request)
    now = time.monotonic()
    with _RUNTIME_RATE_LOCK:
        bucket = _RUNTIME_RATE[path][key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= int(limit):
            raise HTTPException(
                status_code=429,
                detail="Direct execution transport rate limit exceeded",
            )
        bucket.append(now)


def install_vps_direct_runtime_rate_limit() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original = base_api.enforce_personal_mutation_rate_limit

    def enforce_runtime_or_personal(request: Any) -> None:
        path = str(request.url.path or "")
        limit = _RUNTIME_LIMITS.get(path)
        if limit is not None:
            _enforce_direct_runtime_rate(request, path, limit)
            return
        original(request)

    base_api.enforce_personal_mutation_rate_limit = enforce_runtime_or_personal
    base_api._vps_direct_runtime_rate_limit_installed = True
    base_api._vps_direct_runtime_rate_limits = dict(_RUNTIME_LIMITS)
    _INSTALLED = True
