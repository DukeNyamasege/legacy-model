from __future__ import annotations

import re
from pathlib import Path


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(token\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:CR|VRTC|VR|DOT|MF)[A-Z0-9]{5,}\b"),
)

_FORBIDDEN_PATH_PARTS = {
    ".env",
    "tokens.txt",
    "users.json",
    ".runtime_tokens.txt",
    ".runtime_users.json",
    "secrets",
    "model_artifacts",
    "deploy-backups",
    ".deployment_state",
    ".git",
}

_FORBIDDEN_COMMAND_FRAGMENTS = (
    "docker volume rm",
    "docker volume prune",
    "docker system prune --volumes",
    "docker compose down -v",
    "docker-compose down -v",
    "rm -rf /",
    "git push --force",
    "git reset --hard origin/main",
    "DROP DATABASE",
    "TRUNCATE TABLE",
)


def redact(text: str, *, maximum_chars: int = 80_000) -> str:
    value = str(text or "")[:maximum_chars]
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", value)
    return value


def safe_repo_path(repo_dir: Path, relative_path: str) -> Path:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in Path(raw).parts:
        raise ValueError(f"Unsafe repository path: {relative_path!r}")
    parts = set(Path(raw).parts)
    if parts & _FORBIDDEN_PATH_PARTS or any(
        part.startswith(".env") for part in Path(raw).parts
    ):
        raise ValueError(f"Guardian cannot modify protected path: {relative_path}")
    resolved = (repo_dir / raw).resolve()
    if repo_dir.resolve() not in resolved.parents and resolved != repo_dir.resolve():
        raise ValueError(f"Path escapes repository: {relative_path}")
    return resolved


def validate_diff(diff_text: str) -> None:
    text = str(diff_text or "")
    lowered = text.lower()
    for fragment in _FORBIDDEN_COMMAND_FRAGMENTS:
        if fragment.lower() in lowered:
            raise ValueError(f"Forbidden destructive operation detected: {fragment}")
    for line in text.splitlines():
        if not line.startswith(("+++ b/", "--- a/")):
            continue
        path = line[6:].strip()
        if path == "/dev/null":
            continue
        parts = set(Path(path).parts)
        if parts & _FORBIDDEN_PATH_PARTS or any(
            part.startswith(".env") for part in Path(path).parts
        ):
            raise ValueError(f"Patch touches protected path: {path}")


def sanitize_test_command(command: str) -> str:
    value = str(command or "").strip()
    allowed_prefixes = (
        "python -m compileall",
        "python3 -m compileall",
        "python -m unittest",
        "python3 -m unittest",
        "pytest",
        "docker compose config",
        "sh -n ",
        "node --check ",
    )
    if not value.startswith(allowed_prefixes):
        raise ValueError(f"Test command is not allow-listed: {value}")
    if any(fragment.lower() in value.lower() for fragment in _FORBIDDEN_COMMAND_FRAGMENTS):
        raise ValueError(f"Unsafe test command: {value}")
    return value
