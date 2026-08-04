from __future__ import annotations

import copy
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException

import app.api as base_api
from app.production_integration_hardening import _validate_oauth_configuration

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


def install_fast_integration_health(app: Any) -> None:
    """Make /health/integration a bounded cached-state verification.

    The old endpoint forced full Demo and Real accounting rebuilds while the API
    was also serving browsers. One deployment therefore waited 90 seconds on a
    health request even though PostgreSQL, the worker and both containers were
    healthy. Deep accounting work belongs to the background snapshot publisher;
    deployment health verifies the last-good snapshots and never triggers it.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_route(app, "/health/integration", "GET")

    @app.get("/health/integration")
    def fast_integration_health(
        _administrator: str = Depends(base_api.require_control_auth),
    ) -> dict[str, Any]:
        started = time.monotonic()
        checks: dict[str, Any] = {}
        failures: list[str] = []

        def record(name: str, passed: bool, detail: Any) -> None:
            checks[name] = {"ok": bool(passed), "detail": detail}
            if not passed:
                failures.append(name)

        record("database", bool(base_api.DATABASE.ping()), "connected")
        encryption_ready = base_api.has_encryption_key(
            base_api.CONFIG.deriv.token_encryption_key
        )
        record(
            "token_encryption",
            encryption_ready,
            "configured" if encryption_ready else "missing",
        )
        control_ready = bool(os.getenv("CONTROL_API_KEY", "").strip())
        record(
            "control_api_key",
            control_ready,
            "configured" if control_ready else "missing",
        )
        try:
            oauth = _validate_oauth_configuration()
            record("oauth", True, copy.deepcopy(oauth))
        except Exception as exc:
            record("oauth", False, f"{type(exc).__name__}: {exc}"[:300])

        heartbeat_text = str(base_api.REPOSITORY.worker_heartbeat() or "")
        heartbeat_age: float | None = None
        if heartbeat_text:
            try:
                heartbeat = datetime.fromisoformat(heartbeat_text)
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                heartbeat_age = max(
                    0.0,
                    (datetime.now(timezone.utc) - heartbeat).total_seconds(),
                )
            except ValueError:
                heartbeat_age = None
        record(
            "worker_heartbeat",
            heartbeat_age is not None and heartbeat_age <= 45.0,
            {"last_heartbeat": heartbeat_text or None, "age_seconds": heartbeat_age},
        )

        dashboard_details: dict[str, Any] = {}
        for mode in ("demo", "real"):
            try:
                # dashboard_summary is a last-good cache read. It deliberately
                # ignores legacy forced-refresh requests and never runs all-time
                # accounting inside this health endpoint.
                payload = base_api.dashboard_summary(account_type=mode)
                performance = dict(payload.get("system_performance") or {})
                today = dict(performance.get("today") or {})
                total = int(today.get("total_trades") or 0)
                wins = int(today.get("wins") or 0)
                losses = int(today.get("losses") or 0)
                version = int(payload.get("snapshot_version") or 0)
                mode_ok = (
                    not bool(payload.get("snapshot_unavailable"))
                    and payload.get("dashboard_account_type") == mode
                    and total == wins + losses
                    and version > 0
                    and bool(payload.get("generated_at"))
                )
                dashboard_details[mode] = {
                    "ok": mode_ok,
                    "snapshot_version": version,
                    "generated_at": payload.get("generated_at"),
                    "data_age_seconds": payload.get("data_age_seconds"),
                    "refreshing": bool(payload.get("refreshing")),
                    "total_trades": total,
                    "wins": wins,
                    "losses": losses,
                    "source": "last_good_cache",
                }
                if not mode_ok:
                    failures.append(f"dashboard_{mode}")
            except Exception as exc:
                dashboard_details[mode] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                    "source": "last_good_cache",
                }
                failures.append(f"dashboard_{mode}")
        checks["dashboard_modes"] = {
            "ok": all(item.get("ok") for item in dashboard_details.values()),
            "detail": dashboard_details,
        }

        critical_route_counts: dict[str, int] = {}
        for path in (
            "/oauth/start",
            "/oauth/callback",
            "/ws/dashboard",
            "/metrics/summary",
            "/me",
            "/me/trades/today",
            "/me/switch-account",
        ):
            count = sum(
                1
                for route in app.router.routes
                if getattr(route, "path", None) == path
            )
            critical_route_counts[path] = count
            if count != 1:
                failures.append(f"route:{path}")
        checks["critical_routes"] = {
            "ok": all(count == 1 for count in critical_route_counts.values()),
            "detail": critical_route_counts,
        }

        elapsed_ms = round((time.monotonic() - started) * 1000.0, 3)
        checks["latency_budget"] = {
            "ok": elapsed_ms < 5000.0,
            "detail": {
                "elapsed_ms": elapsed_ms,
                "budget_ms": 5000.0,
                "forced_dashboard_rebuild": False,
            },
        }
        if elapsed_ms >= 5000.0:
            failures.append("latency_budget")

        response = {
            "status": "ready" if not failures else "not_ready",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "model_version": base_api.CONFIG.model.version,
            "transport": "private_deriv_websocket",
            "health_profile": "cached-nonblocking-v1",
            "elapsed_ms": elapsed_ms,
            "checks": checks,
            "failures": failures,
        }
        if failures:
            raise HTTPException(status_code=503, detail=response)
        return response

    app.state.fast_integration_health_installed = True
    app.state.integration_health_profile = "cached-nonblocking-v1"
    _INSTALLED = True
