from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

import app.api as base_api


_INSTALLED = False
_LAST_DATABASE_WARNING_AT = 0.0


def install_database_runtime_hardening(app: Any) -> None:
    """Expose DB-aware health and convert connection outages into controlled 503s."""
    global _INSTALLED
    if _INSTALLED:
        return

    # Keep one authoritative database-health route even when legacy wrappers are
    # imported in a different order.
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) != "/health/database"
    ]

    async def database_operational_error_handler(
        request: Request,
        _exc: OperationalError,
    ) -> JSONResponse:
        global _LAST_DATABASE_WARNING_AT
        now = time.monotonic()
        if now - _LAST_DATABASE_WARNING_AT >= 30.0:
            _LAST_DATABASE_WARNING_AT = now
            base_api.LOGGER.warning(
                "DATABASE_REQUEST_UNAVAILABLE path=%s response_status=503",
                request.url.path,
            )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Database temporarily unavailable. The service will recover automatically when PostgreSQL is reachable."
            },
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        )

    app.add_exception_handler(OperationalError, database_operational_error_handler)

    @app.get("/health/database", include_in_schema=False)
    def database_health() -> dict[str, str]:
        if not base_api.DATABASE.ping():
            raise HTTPException(status_code=503, detail="Database unavailable")
        return {
            "status": "ready",
            "service": "test2-api",
            "database": "connected",
        }

    _INSTALLED = True
