"""Run the Test 2 API and trading worker together for local evaluation."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def main() -> None:
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("DERIV_BOT_CONFIG", str(ROOT / "config.yaml"))
    os.environ.setdefault("TRADING_MODE", "demo")
    os.environ.setdefault("ALLOW_REAL_TRADING", "false")
    os.environ.setdefault("DEPLOYMENT_ID", "local")
    os.environ["LOCAL_CONTROL_ENABLED"] = "true"
    if not os.getenv("CONTROL_API_KEY"):
        os.environ["CONTROL_API_KEY"] = secrets.token_urlsafe(24)

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))
    sync_url = os.getenv("NETLIFY_SYNC_URL", "").strip()
    sync_token = os.getenv("NETLIFY_SYNC_TOKEN", "").strip()
    sync_interval = os.getenv("NETLIFY_SYNC_INTERVAL_SECONDS", "15").strip() or "15"
    print(f"Dashboard: http://{host}:{port}")
    print(f"Control key: {os.environ['CONTROL_API_KEY']}")
    if sync_url and sync_token:
        print(
            "Netlify mirror sync: enabled "
            f"({sync_url}, every {sync_interval}s)"
        )
    else:
        missing = []
        if not sync_url:
            missing.append("NETLIFY_SYNC_URL")
        if not sync_token:
            missing.append("NETLIFY_SYNC_TOKEN")
        print(
            "Netlify mirror sync: disabled "
            f"(missing {', '.join(missing)})"
        )
    print("The worker logs will remain visible in this terminal.")

    worker = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=ROOT,
        env=os.environ.copy(),
    )
    try:
        try:
            worker.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        else:
            raise RuntimeError(
                f"Trading worker exited during startup with code {worker.returncode}"
            )
        uvicorn.run(
            "app.api:app",
            host=host,
            port=port,
            reload=False,
            access_log=False,
        )
    finally:
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=15)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()


if __name__ == "__main__":
    main()
