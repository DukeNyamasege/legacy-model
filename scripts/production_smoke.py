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
READINESS_PROBE_TIMEOUT_SECONDS = 30.0


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), value)


def _positive_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


PROVIDER_WS_ATTEMPTS = _positive_int("DEPLOY_PROVIDER_WS_ATTEMPTS", 5)
PROVIDER_WS_OPEN_TIMEOUT_SECONDS = _positive_float(
    "DEPLOY_PROVIDER_WS_OPEN_TIMEOUT_SECONDS",
    20.0,
    5.0,
)
PROVIDER_WS_BACKOFF_BASE_SECONDS = _positive_float(
    "DEPLOY_PROVIDER_WS_BACKOFF_BASE_SECONDS",
    5.0,
    1.0,
)
PROVIDER_WS_BACKOFF_MAX_SECONDS = _positive_float(
    "DEPLOY_PROVIDER_WS_BACKOFF_MAX_SECONDS",
    30.0,
    PROVIDER_WS_BACKOFF_BASE_SECONDS,
)


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
    probe_timeout = max(5.0, min(READINESS_PROBE_TIMEOUT_SECONDS, timeout_seconds / 2))
    while time.monotonic() < deadline:
        try:
            response = session.get(f"{base_url}/health/ready", timeout=probe_timeout)
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


async def _verify_deriv_public_websocket_once(url: str, req_id: int) -> dict[str, Any]:
    async with websockets.connect(
        url,
        open_timeout=PROVIDER_WS_OPEN_TIMEOUT_SECONDS,
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
            if "error" in response:
                raise SmokeFailure(
                    "Deriv public WebSocket error: "
                    + json.dumps(response.get("error"), sort_keys=True)[:700]
                )
            require(
                response.get("msg_type") == "ping",
                "Unexpected Deriv public WebSocket response: "
                + json.dumps(response, sort_keys=True)[:700],
            )
            return {"url": url, "msg_type": response.get("msg_type")}


async def verify_deriv_public_websocket(url: str) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, PROVIDER_WS_ATTEMPTS + 1):
        req_id = 760731 + attempt
        try:
            result = await _verify_deriv_public_websocket_once(url, req_id)
            result["attempts"] = attempt
            result["retry_policy"] = "bounded_exponential"
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            errors.append(error)
            if attempt >= PROVIDER_WS_ATTEMPTS:
                break
            delay = min(
                PROVIDER_WS_BACKOFF_MAX_SECONDS,
                PROVIDER_WS_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
            )
            print(
                json.dumps(
                    {
                        "event": "DERIV_PUBLIC_WS_SMOKE_RETRY",
                        "attempt": attempt,
                        "maximum_attempts": PROVIDER_WS_ATTEMPTS,
                        "error": error,
                        "retry_seconds": delay,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(delay)

    raise SmokeFailure(
        "Deriv public WebSocket remained unavailable after "
        f"{PROVIDER_WS_ATTEMPTS} bounded attempts: "
        + " | ".join(errors[-3:])
    )


def _automatic_provider_skip() -> bool:
    deployment_id = os.getenv("DEPLOYMENT_ID", "").strip().lower()
    return deployment_id.startswith("preflight-api")


async def async_checks(base_url: str, public_ws_url: str, skip_provider: bool) -> dict[str, Any]:
    dashboard = {}
    for mode in ("demo", "real"):
        dashboard[mode] = await verify_dashboard_websocket(base_url, mode)
    provider = {
        "skipped": True,
        "reason": "isolated_preflight_avoids_duplicate_provider_connection",
    }
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

    # The public root is now the stable dashboard-v2 shell. It deliberately does
    # not inject the old realtime/data-consistency/custom-Martingale DOM scripts,
    # because those legacy observers conflict with the renderer and can make
    # Chrome unresponsive. Validate the UI that production actually serves.
    html_response = session.get(f"{base_url}/", timeout=20)
    require(html_response.status_code == 200, f"Dashboard HTML returned {html_response.status_code}")
    html_text = html_response.text
    require(
        "/ui/dashboard-v2.css" in html_text,
        "dashboard-v2 stylesheet is not present in dashboard HTML",
    )
    require(
        "/ui/dashboard-v2.js" in html_text,
        "dashboard-v2 script is not present in dashboard HTML",
    )
    require(
        "/ui/dashboard-actions-v2.js" in html_text,
        "dashboard actions script is not present in dashboard HTML",
    )
    require(
        "Father of Automation" in html_text,
        "Dashboard brand marker is missing",
    )
    require(
        "/ui/simplified-dashboard.js" in html_text,
        "Historical simplified-dashboard compatibility marker is missing",
    )
    for legacy_marker in (
        "/ui/realtime-mode-hardening.js",
        "/ui/data-consistency.js",
        "smart-loader",
    ):
        require(
            legacy_marker not in html_text,
            f"Legacy dashboard runtime is still present: {legacy_marker}",
        )

    dashboard_script = session.get(f"{base_url}/ui/dashboard-v2.js", timeout=20)
    require(
        dashboard_script.status_code == 200,
        f"dashboard-v2 JavaScript returned {dashboard_script.status_code}",
    )
    dashboard_text = dashboard_script.text
    for marker in (
        "foa-simple-app",
        "foa-session-v2",
        "window.FOA_BOOT_SESSION",
        "function switchMode(mode)",
        "/metrics/summary",
        "/me",
    ):
        require(marker in dashboard_text, f"dashboard-v2 JavaScript is missing {marker!r}")

    actions_script = session.get(f"{base_url}/ui/dashboard-actions-v2.js", timeout=20)
    require(
        actions_script.status_code == 200,
        f"dashboard actions JavaScript returned {actions_script.status_code}",
    )
    actions_text = actions_script.text
    for marker in (
        "foa-action-loader",
        "foa-final-trade-row",
        "clear-trades",
    ):
        require(marker in actions_text, f"Dashboard actions JavaScript is missing {marker!r}")

    stylesheet = session.get(f"{base_url}/ui/dashboard-v2.css", timeout=20)
    require(
        stylesheet.status_code == 200,
        f"dashboard-v2 stylesheet returned {stylesheet.status_code}",
    )
    stylesheet_text = stylesheet.text
    for marker in (
        "foa-simple-app",
        "foa-bottom-nav",
        "font-size:16px",
        "min-height:44px",
    ):
        require(marker in stylesheet_text.replace(" ", ""), f"dashboard-v2 stylesheet is missing {marker!r}")

    simplified_script = session.get(f"{base_url}/ui/simplified-dashboard.js", timeout=20)
    require(
        simplified_script.status_code == 200,
        f"Simplified dashboard JavaScript returned {simplified_script.status_code}",
    )
    simplified_text = simplified_script.text
    for marker in (
        "foa-simple-app",
        "window.FOA_BOOT_SESSION",
        "foa-session-v2",
    ):
        require(marker in simplified_text, f"Simplified dashboard JavaScript is missing {marker!r}")

    # The advanced Martingale endpoint remains available for account settings and
    # compatibility tests even though its old DOM injector is not loaded at root.
    martingale_script = session.get(f"{base_url}/custom-martingale.js", timeout=20)
    require(
        martingale_script.status_code == 200,
        f"Custom Martingale JavaScript returned {martingale_script.status_code}",
    )
    script_text = martingale_script.text
    for marker in (
        "personal-martingale-mode",
        "martingale_trigger_losses",
        "martingale_multiplier",
        "System Martingale",
        "Custom Martingale",
    ):
        require(marker in script_text, f"Custom Martingale JavaScript is missing {marker!r}")

    report["checks"]["dashboard_html"] = {
        "bytes": len(html_response.content),
        "dashboard_v2_ui": True,
        "legacy_runtime_absent": True,
    }
    report["checks"]["dashboard_v2"] = {
        "script_bytes": len(dashboard_script.content),
        "actions_bytes": len(actions_script.content),
        "stylesheet_bytes": len(stylesheet.content),
        "session_bootstrap_supported": True,
        "mobile_input_zoom_guard": True,
    }
    report["checks"]["simplified_dashboard_compat"] = {
        "script_bytes": len(simplified_script.content),
    }
    report["checks"]["custom_martingale"] = {
        "script_bytes": len(martingale_script.content),
        "system_default": True,
        "custom_trigger_and_multiplier": True,
        "legacy_dom_injector_not_loaded_at_root": True,
    }

    report["checks"]["oauth_start"] = validate_oauth_start(session, base_url, config)
    skip_provider = bool(args.skip_provider) or _automatic_provider_skip()
    report["checks"].update(
        asyncio.run(
            async_checks(
                base_url,
                str(config.deriv.public_ws_url),
                skip_provider,
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
