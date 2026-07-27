from __future__ import annotations

import os
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


_INSTALLED = False


def install_api_security_hardening(app: Any) -> None:
    """Add defense-in-depth around the existing API security controls.

    The base API already enforces trusted hosts, explicit CORS origins, mutation
    origin checks, personal/control rate limits, secure cookies, control API
    authentication and a restrictive CSP. This layer closes avoidable discovery
    surfaces and adds request/header hardening without changing trading routes.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    # Production users do not need an automatically generated inventory of every
    # route and request model. Removing these routes reduces casual API discovery;
    # authorization remains the real protection for privileged endpoints.
    hidden_paths = {"/docs", "/redoc", "/openapi.json"}
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) not in hidden_paths
    ]

    max_body_bytes = max(
        131_072,
        int(os.getenv("MAX_HTTP_REQUEST_BODY_BYTES", "262144")),
    )

    @app.middleware("http")
    async def production_security_hardening(request: Request, call_next):
        method = request.method.upper()
        if method in {"TRACE", "CONNECT"}:
            return JSONResponse(
                status_code=405,
                content={"detail": "HTTP method not allowed"},
            )

        content_length = request.headers.get("content-length", "").strip()
        if content_length:
            try:
                body_size = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length"},
                )
            if body_size > max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )

        response = await call_next(request)

        # Avoid unnecessary implementation disclosure and browser persistence of
        # sensitive/account-scoped responses.
        if "server" in response.headers:
            del response.headers["server"]
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["Origin-Agent-Cluster"] = "?1"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"

        path = request.url.path
        if path == "/" or path.startswith("/ui/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"

        return response

    _INSTALLED = True
