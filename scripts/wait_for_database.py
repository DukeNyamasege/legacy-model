from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Final

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


DEFAULT_TIMEOUT_SECONDS: Final[float] = 180.0
DEFAULT_INTERVAL_SECONDS: Final[float] = 2.0


def _safe_error(exc: BaseException) -> str:
    """Return one safe error line without leaking credentials from a database URL."""
    value = str(exc).splitlines()[0].strip()
    value = re.sub(r"(?i)(postgres(?:ql)?(?:\+\w+)?://)[^@\s]+@", r"\1***@", value)
    return value[:500]


def wait_for_database(*, timeout_seconds: float, interval_seconds: float) -> None:
    database_url = str(os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    parsed = make_url(database_url)
    host = str(parsed.host or "unknown")
    port = int(parsed.port or 5432)
    database = str(parsed.database or "unknown")
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    attempt = 0
    last_error = "database connection has not been attempted"

    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        connect_args={"connect_timeout": 5},
    )
    try:
        while True:
            attempt += 1
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                print(
                    "DATABASE_READY "
                    f"host={host} port={port} database={database} attempts={attempt}",
                    flush=True,
                )
                return
            except Exception as exc:  # startup must tolerate DNS and DB readiness races
                last_error = f"{type(exc).__name__}: {_safe_error(exc)}"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                print(
                    "DATABASE_WAIT "
                    f"host={host} port={port} attempt={attempt} "
                    f"remaining_seconds={max(0, int(remaining))} error={last_error}",
                    flush=True,
                )
                engine.dispose()
                time.sleep(min(max(0.2, interval_seconds), max(0.2, remaining)))
    finally:
        engine.dispose()

    raise RuntimeError(
        "database did not become reachable before timeout: "
        f"host={host} port={port} database={database} last_error={last_error}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait until PostgreSQL is reachable.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    args = parser.parse_args()
    try:
        wait_for_database(
            timeout_seconds=args.timeout,
            interval_seconds=args.interval,
        )
    except Exception as exc:
        print(f"DATABASE_UNAVAILABLE error={_safe_error(exc)}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
