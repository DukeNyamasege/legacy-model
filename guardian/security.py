from __future__ import annotations

import re
from pathlib import Path


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(token\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
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

_MANUAL_ONLY_PATHS = {
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.vps.yml",
    "alembic.ini",
    "config.yaml",
    "scripts/deploy_vps.sh",
    "scripts/update_vps.sh",
    "scripts/install_guardian.sh",
    "deploy/legacy-model-guardian.service",
    ".env.guardian.example",
    "guardian/PROJECT_CHARTER.md",
    "guardian/security.py",
    "guardian/sandbox.py",
    "guardian/patcher.py",
}

_MANUAL_ONLY_PREFIXES = (
    "alembic/",
    ".github/workflows/",
    "deploy/",
)

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
        value = pattern.sub(
            lambda match: (match.group(1) if match.lastindex else "")
            + "[REDACTED]",
            value,
        )
    return value


def _normalise_relative_path(relative_path: str) -> str:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in Path(raw).parts:
        raise ValueError(f"Unsafe repository path: {relative_path!r}")
    parts = set(Path(raw).parts)
    if parts & _FORBIDDEN_PATH_PARTS or any(
        part.startswith(".env") for part in Path(raw).parts
    ):
        raise ValueError(f"Guardian cannot modify protected path: {relative_path}")
    if raw in _MANUAL_ONLY_PATHS or any(
        raw.startswith(prefix) for prefix in _MANUAL_ONLY_PREFIXES
    ):
        raise ValueError(
            f"Guardian cannot automatically modify manual-review path: {relative_path}"
        )
    return raw


def safe_repo_path(repo_dir: Path, relative_path: str) -> Path:
    raw = _normalise_relative_path(relative_path)
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
        _normalise_relative_path(path)


def sanitize_test_command(command: str) -> str:
    value = str(command or "").strip()
    allowed_prefixes = (
        "python -m compileall",
        "python3 -m compileall",
        "python -m unittest",
        "python3 -m unittest",
        "pytest",
        "sh -n ",
    )
    if not value.startswith(allowed_prefixes):
        raise ValueError(f"Test command is not allow-listed: {value}")
    if any(fragment.lower() in value.lower() for fragment in _FORBIDDEN_COMMAND_FRAGMENTS):
        raise ValueError(f"Unsafe test command: {value}")
    return value
