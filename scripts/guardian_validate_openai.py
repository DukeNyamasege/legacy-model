from __future__ import annotations

import argparse
from pathlib import Path

from openai import OpenAI


def _environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Guardian OpenAI credentials and configured model access."
    )
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    values = _environment(args.env_file)
    api_key = values.get("OPENAI_API_KEY", "")
    models = {
        values.get("GUARDIAN_DIAGNOSIS_MODEL", "gpt-5.4-mini"),
        values.get("GUARDIAN_CODING_MODEL", "gpt-5.3-codex"),
        values.get("GUARDIAN_REVIEWER_MODEL", "gpt-5.4-mini"),
    }
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")
    if "" in models:
        raise RuntimeError("A Guardian model name is missing")

    client = OpenAI(api_key=api_key, timeout=30.0)
    for model in sorted(models):
        result = client.models.retrieve(model)
        print(f"OpenAI model access verified: {result.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
