from __future__ import annotations

import copy
import os
import threading
import time
from typing import Any

import app.api as base_api


_INSTALLED = False
_ORIGINAL_BUILD = None
_CACHE_LOCK = threading.RLock()
_MODE_LOCKS = {
    "demo": threading.Lock(),
    "real": threading.Lock(),
}
_CACHE: dict[str, tuple[float, tuple[dict, Any, dict[str, object]]]] = {}


def _ttl_seconds() -> float:
    try:
        configured = float(os.getenv("DASHBOARD_REBUILD_MIN_INTERVAL_SECONDS", "15"))
    except (TypeError, ValueError):
        configured = 15.0
    return min(120.0, max(5.0, configured))


def _cached_snapshot(mode: str) -> tuple[dict, Any, dict[str, object]] | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(mode)
        if cached is None or now >= cached[0]:
            return None
        return copy.deepcopy(cached[1])


def _bounded_snapshot_build(account_type: str):
    """Build each public dashboard mode at most once per bounded interval.

    Settlement events can arrive many times per second across hundreds of users.
    They should mark the snapshot dirty, but they must not repeatedly replay all
    historical model trades and scan account summaries while request handlers are
    serving `/metrics/summary`, `/me` and readiness probes.
    """

    mode = base_api.normalize_account_type(account_type)
    cached = _cached_snapshot(mode)
    if cached is not None:
        return cached

    lock = _MODE_LOCKS[mode]
    with lock:
        cached = _cached_snapshot(mode)
        if cached is not None:
            return cached
        original = _ORIGINAL_BUILD
        if original is None:
            raise RuntimeError("Original dashboard snapshot builder is unavailable")
        built = original(mode)
        expires_at = time.monotonic() + _ttl_seconds()
        with _CACHE_LOCK:
            _CACHE[mode] = (expires_at, copy.deepcopy(built))
        return built


def invalidate_dashboard_snapshot_throttle(account_type: str | None = None) -> None:
    """Clear process-local throttle data after an explicit administrative reset."""

    with _CACHE_LOCK:
        if account_type is None:
            _CACHE.clear()
        else:
            _CACHE.pop(base_api.normalize_account_type(account_type), None)


def install_dashboard_snapshot_throttle() -> None:
    global _INSTALLED, _ORIGINAL_BUILD
    if _INSTALLED:
        return
    builder = getattr(base_api, "_build_dashboard_snapshot", None)
    if not callable(builder):
        raise RuntimeError("Dashboard snapshot builder is unavailable")
    _ORIGINAL_BUILD = builder
    base_api._build_dashboard_snapshot = _bounded_snapshot_build
    base_api.invalidate_dashboard_snapshot_throttle = (
        invalidate_dashboard_snapshot_throttle
    )
    base_api._dashboard_snapshot_throttle_installed = True
    _INSTALLED = True
