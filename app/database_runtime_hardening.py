from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import OperationalError

import app.api as base_api


_INSTALLED = False
_LAST_DATABASE_WARNING_AT = 0.0


def install_database_runtime_hardening(app: Any) -> None:
    """Expose DB-aware health and convert connection outages into controlled 503s."""
    global _INSTALLED
    if _INSTALLED:
        return

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

    @app.head("/health/database", include_in_schema=False)
    def database_health_head() -> Response:
        if not base_api.DATABASE.ping():
            raise HTTPException(status_code=503, detail="Database unavailable")
        return Response(
            content=b"",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "Content-Type": "application/json",
            },
        )

    from app.final_execution_alert_api import install_final_execution_alert_api
    from app.execution_alert_refinement import install_execution_alert_refinement
    from app.seamless_personal_execution import install_seamless_personal_execution
    from app.seamless_personal_execution_final import (
        install_final_seamless_personal_execution,
    )
    from app.manual_martingale_v2 import install_manual_martingale_v2_api
    from app.trading_controls_final_ui import install_trading_controls_final_ui

    install_final_execution_alert_api(app)
    install_execution_alert_refinement(app)
    install_seamless_personal_execution(app)
    install_final_seamless_personal_execution(app)

    # Final personal recovery controls are deliberately installed after every
    # lifecycle/compatibility layer. The System Strategy remains locked to its
    # native AIDR recovery while manual families receive their own policy API.
    install_manual_martingale_v2_api(app)

    # Serve the final dashboard script last so Wins/Losses KPI cards and the
    # manual Martingale selector cannot be overwritten by an older UI compositor.
    install_trading_controls_final_ui(app)

    _INSTALLED = True
