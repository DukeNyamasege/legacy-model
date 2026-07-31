#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests
import websockets
from dotenv import load_dotenv

from app.config import load_test2_config


load_dotenv(ROOT / ".env")
REDIRECT_STATUS_CODES = {302, 303, 307, 308}


class SmokeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    response = session.request(
        method,
        url,
        headers=headers,
        timeout=timeout,
        allow_redirects=False,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:500]}
    if response.status_code >= 400:
        raise SmokeFailure(
            f"{method} {url} returned {response.status_code}: {json.dumps(payload)[:700]}"
        )
    require(isinstance(payload, dict), f"{method} {url} did not return a JSON object")
    return payload


def wait_for_ready(
    session: requests.Session,
    base_url: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not checked"
    while time.monotonic() < deadline:
        try:
            response = session.get(f"{base_url}/health/ready", timeout=5)
            if response.status_code == 200:
                payload = response.json()
                require(payload.get("status") == "ready", "Readiness payload is not ready")
                return payload
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
    raise SmokeFailure(f"API/worker readiness timed out: {last_error}")


def assert_dashboard_snapshot(payload: dict[str, Any], mode: str) -> None:
    require(payload.get("dashboard_account_type") == mode, f"Dashboard mode mismatch for {mode}")
    require(int(payload.get("snapshot_version") or 0) > 0, f"{mode} snapshot has no version")
    require(bool(payload.get("generated_at")), f"{mode} snapshot has no generated_at")
    system = payload.get("system_performance") or {}
    today = system.get("today") or {}
    total = int(today.get("total_trades") or 0)
    wins = int(today.get("wins") or 0)
    losses = int(today.get("losses") or 0)
    require(total == wins + losses, f"{mode} dashboard invariant failed: {total} != {wins}+{losses}")


def validate_oauth_start(
    session: requests.Session,
    base_url: str,
    config,
) -> dict[str, Any]:
    response = session.get(f"{base_url}/oauth/start", timeout=20, allow_redirects=False)
    require(
        response.status_code in REDIRECT_STATUS_CODES,
        f"OAuth start returned {response.status_code}",
    )
    location = response.headers.get("location", "")
    parsed = urlparse(location)
    require(parsed.scheme == "https", "OAuth authorization redirect is not HTTPS")
    require(parsed.netloc == "auth.deriv.com", f"Unexpected OAuth host: {parsed.netloc}")
    require(parsed.path == "/oauth2/auth", f"Unexpected OAuth path: {parsed.path}")
    query = parse_qs(parsed.query)
    required = {
        "response_type": "code",
        "client_id": str(config.deriv.oauth_client_id or config.deriv.app_id),
        "redirect_uri": str(config.deriv.oauth_redirect_url),
        "code_challenge_method": "S256",
    }
    for key, expected in required.items():
        actual = str((query.get(key) or [""])[0])
        require(actual == expected, f"OAuth {key} mismatch: {actual!r} != {expected!r}")
    require(bool((query.get("state") or [""])[0]), "OAuth state is missing")
    require(bool((query.get("code_challenge") or [""])[0]), "OAuth PKCE challenge is missing")
    scopes = set(str((query.get("scope") or [""])[0]).split())
    expected_scopes = set(
        os.getenv("DERIV_OAUTH_SCOPES", "trade application_read").replace(",", " ").split()
    )
    require(scopes == expected_scopes, f"OAuth scopes mismatch: {scopes} != {expected_scopes}")
    require("trade" in scopes, "OAuth trade scope is missing")

    rejected = session.get(
        f"{base_url}/oauth/callback",
        params={"code": "smoke-invalid-code", "state": "smoke-invalid-state"},
        timeout=20,
        allow_redirects=False,
    )
    require(
        rejected.status_code in REDIRECT_STATUS_CODES,
        f"Invalid OAuth callback returned non-redirect status {rejected.status_code}",
    )
    rejected_location = rejected.headers.get("location", "")
    rejected_url = urlparse(rejected_location)
    rejected_query = parse_qs(rejected_url.query)
    require(
        not rejected_url.scheme and not rejected_url.netloc and rejected_url.path == "/",
        f"Invalid OAuth callback redirected outside the local dashboard: {rejected_location!r}",
    )
    oauth_errors = [
        str(value).strip()
        for value in rejected_query.get("oauth_error", [])
        if str(value).strip()
    ]
    require(bool(oauth_errors), "Invalid OAuth state did not produce a safe error redirect")
    require(
        "code" not in rejected_query and "state" not in rejected_query,
        "Invalid OAuth callback leaked code or state into the error redirect",
    )

    return {
        "host": parsed.netloc,
        "redirect_uri": required["redirect_uri"],
        "scopes": sorted(scopes),
        "invalid_state_rejected": True,
        "invalid_state_status": rejected.status_code,
    }


def websocket_url(base_url: str, path: str, query: dict[str, str] | None = None) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(
        (
            scheme,
            parsed.netloc,
            path,
            "",
            urlencode(query or {}),
            "",
        )
    )


async def verify_dashboard_websocket(base_url: str, mode: str) -> dict[str, Any]:
    url = websocket_url(base_url, "/ws/dashboard", {"mode": mode})
    async with websockets.connect(
        url,
        open_timeout=15,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
    ) as websocket:
        raw = await asyncio.wait_for(websocket.recv(), timeout=30)
        message = json.loads(raw)
        require(message.get("type") == "snapshot", f"{mode} WebSocket sent no snapshot")
        require(message.get("mode") == mode, f"{mode} WebSocket envelope mode mismatch")
        payload = message.get("data") or {}
        assert_dashboard_snapshot(payload, mode)
        return {
            "mode": mode,
            "snapshot_version": int(payload.get("snapshot_version") or 0),
        }


async def verify_deriv_public_websocket(url: str) -> dict[str, Any]:
    req_id = 760731
    async with websockets.connect(
        url,
        open_timeout=15,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
    ) as websocket:
        await websocket.send(json.dumps({"ping": 1, "req_id": req_id}))
        deadline = asyncio.get_running_loop().time() + 20
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            require(remaining > 0, "Deriv public WebSocket ping timed out")
            response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=remaining))
            if response.get("req_id") != req_id:
                continue
            require("error" not in response, f"Deriv public WebSocket error: {response['error']}")
            require(response.get("msg_type") == "ping", "Unexpected Deriv ping response")
            return {"url": url, "msg_type": response.get("msg_type")}


async def async_checks(base_url: str, public_ws_url: str, skip_provider: bool) -> dict[str, Any]:
    dashboard = {}
    for mode in ("demo", "real"):
        dashboard[mode] = await verify_dashboard_websocket(base_url, mode)
    provider = {"skipped": True}
    if not skip_provider:
        provider = await verify_deriv_public_websocket(public_ws_url)
    return {"dashboard_websocket": dashboard, "deriv_public_websocket": provider}


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end production deployment smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--ready-timeout", type=float, default=120.0)
    parser.add_argument("--skip-provider", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    config = load_test2_config(os.getenv("DERIV_BOT_CONFIG", ROOT / "config.yaml"))
    api_key = os.getenv("CONTROL_API_KEY", "").strip()
    require(bool(api_key), "CONTROL_API_KEY is required for production smoke tests")

    report: dict[str, Any] = {
        "base_url": base_url,
        "model_version": config.model.version,
        "checks": {},
    }
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    live = request_json(session, "GET", f"{base_url}/health/live")
    require(live.get("status") == "live", "Liveness endpoint is not live")
    report["checks"]["liveness"] = live

    report["checks"]["readiness"] = wait_for_ready(
        session,
        base_url,
        timeout_seconds=max(10.0, args.ready_timeout),
    )

    integration = request_json(
        session,
        "GET",
        f"{base_url}/health/integration",
        headers={"X-API-Key": api_key},
        timeout=90,
    )
    require(integration.get("status") == "ready", "Integration health is not ready")
    report["checks"]["integration"] = integration

    for mode in ("demo", "real"):
        payload = request_json(
            session,
            "GET",
            f"{base_url}/metrics/summary?mode={mode}",
            timeout=60,
        )
        assert_dashboard_snapshot(payload, mode)
        report["checks"][f"dashboard_{mode}"] = {
            "snapshot_version": int(payload.get("snapshot_version") or 0),
            "generated_at": payload.get("generated_at"),
        }

    html_response = session.get(f"{base_url}/", timeout=20)
    require(html_response.status_code == 200, f"Dashboard HTML returned {html_response.status_code}")
    require(
        "/ui/realtime-mode-hardening.js" in html_response.text,
        "Realtime mode hardening script is not injected into dashboard HTML",
    )
    report["checks"]["dashboard_html"] = {"bytes": len(html_response.content)}

    report["checks"]["oauth_start"] = validate_oauth_start(session, base_url, config)
    report["checks"].update(
        asyncio.run(
            async_checks(
                base_url,
                str(config.deriv.public_ws_url),
                bool(args.skip_provider),
            )
        )
    )

    print(json.dumps({"status": "passed", **report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1)
