#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api import REPOSITORY  # noqa: E402


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Allow the worker to publish a queued deployment announcement."
    )
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()

    release_id = str(args.release_id or "").strip().lower()
    if not COMMIT_RE.fullmatch(release_id):
        raise SystemExit("release-id must be one full 40-character Git commit SHA")

    key = f"telegram_deployment_release_ready:{release_id}"
    REPOSITORY.set_runtime_preference(key, "ready")
    print(f"DEPLOYMENT_RELEASE_READY release_id={release_id[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
