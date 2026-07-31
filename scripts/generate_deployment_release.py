#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import re
import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_BULLETS = 20


class ReleaseGenerationError(RuntimeError):
    pass


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise ReleaseGenerationError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def valid_commit(value: str) -> bool:
    if not value:
        return False
    result = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{value}^{{commit}}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def is_ancestor(older: str, newer: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", older, newer],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def sanitize_subject(subject: str) -> str:
    text = str(subject or "").strip()
    text = re.sub(
        r"^(?:feat|fix|perf|refactor|security|chore|build|ci|docs|test)(?:\([^)]*\))?:\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"https?://\S+", "the service endpoint", text, flags=re.I)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[redacted]", text)
    text = re.sub(
        r"\b(?:pat_|ory_at_)[A-Za-z0-9._-]{12,}\b",
        "[redacted credential]",
        text,
        flags=re.I,
    )
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "[redacted host]", text)
    text = re.sub(r"\b[A-Fa-f0-9]{32,}\b", "[redacted identifier]", text)
    text = re.sub(r"\b[A-Z]{2,6}\d{4,}\b", "[redacted account]", text)
    text = re.sub(r"\s+", " ", text).strip(" .:-")
    if not text:
        return ""
    return text[0].upper() + text[1:]


def category_for(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in ("telegram", "announcement", "release note", "deployment message")):
        return "Community update"
    if any(word in lower for word in ("oauth", "login", "authentication", "pkce", "scope")):
        return "Authentication"
    if any(word in lower for word in ("dashboard", "websocket", "realtime", "real-time", "ui")):
        return "Dashboard"
    if any(word in lower for word in ("postgres", "database", "alembic", "migration", "dns")):
        return "Database"
    if any(word in lower for word in ("purchase", "contract", "settlement", "balance", "trade", "execution")):
        return "Trading"
    if any(word in lower for word in ("security", "encryption", "token", "credential", "secret")):
        return "Security"
    if any(word in lower for word in ("strategy", "over2", "over 2", "under7", "under 7", "signal", "recovery", "virtual")):
        return "Strategy"
    if any(word in lower for word in ("deploy", "docker", "vps", "health", "smoke", "startup", "worker", "api")):
        return "Reliability"
    return "System"


def release_subjects(previous: str, current: str) -> list[str]:
    if previous == current:
        return []
    if valid_commit(previous) and is_ancestor(previous, current):
        revision = f"{previous}..{current}"
    else:
        parent = git("rev-parse", f"{current}^", check=False)
        revision = f"{parent}..{current}" if valid_commit(parent) else current

    output = git("log", "--reverse", "--no-merges", "--format=%s", revision)
    ignored_prefixes = (
        "merge ",
        "document ",
        "update readme",
        "add production architecture",
        "ignore local deployment release state",
    )
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in output.splitlines():
        subject = sanitize_subject(raw)
        if not subject or subject.lower().startswith(ignored_prefixes):
            continue
        key = re.sub(r"[^a-z0-9]+", " ", subject.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(subject)
    return cleaned


def grouped_updates(subjects: list[str]) -> list[str]:
    """Collapse implementation commits while preserving unmatched future changes."""
    lower_subjects = [subject.lower() for subject in subjects]
    consumed: set[int] = set()
    updates: list[str] = []

    rules: tuple[tuple[str, tuple[str, ...], str], ...] = (
        (
            "Community update",
            ("telegram", "announcement", "release note", "one-command vps update"),
            "Deployment announcements now describe the exact changes in each release, exclude sensitive details, and are sent only once per deployed version.",
        ),
        (
            "Authentication",
            ("oauth", "pkce", "authentication", "scope", "redirect"),
            "Login and account linking were strengthened with strict OAuth state, PKCE, redirect and least-privilege scope checks.",
        ),
        (
            "Database",
            ("database startup", "postgres", "alembic", "migration", "docker dns"),
            "Database startup, Docker service discovery and migration readiness were strengthened before the API and worker can start.",
        ),
        (
            "Dashboard",
            ("dashboard", "realtime", "real-time", "websocket", "settlement delivery"),
            "Demo and Real dashboard data now stays account-mode correct and refreshes promptly after settlements and balance updates.",
        ),
        (
            "Trading",
            ("private websocket", "private webSocket", "purchase", "contract", "settlement", "balance policy"),
            "Proposal, authenticated purchase, contract monitoring, settlement reconciliation and post-trade balance delivery were strengthened.",
        ),
        (
            "Strategy",
            ("over2", "over 2", "under7", "under 7", "strategy", "signal gate", "recovery"),
            "Obsolete strategy gates were removed and the active entry, recovery and account-protection rules were aligned with the current model.",
        ),
        (
            "Reliability",
            ("deploy", "smoke test", "health", "startup", "worker integration", "syntax"),
            "Deployment now validates builds, database readiness, API/worker health, provider connectivity and dashboard integration before reporting success.",
        ),
    )

    for category, keywords, summary in rules:
        matched = [
            index
            for index, lower in enumerate(lower_subjects)
            if index not in consumed and any(keyword.lower() in lower for keyword in keywords)
        ]
        if not matched:
            continue
        consumed.update(matched)
        updates.append(f"{category}: {summary}")

    for index, subject in enumerate(subjects):
        if index in consumed:
            continue
        updates.append(f"{category_for(subject)}: {subject}")
    return updates


def build_message(subjects: list[str]) -> str:
    if not subjects:
        return ""

    updates = grouped_updates(subjects)
    visible = updates[:MAX_BULLETS]
    lines = [
        "🚀 NEW SYSTEM DEPLOYMENT",
        "",
        "A new platform version has been deployed successfully.",
        "",
        "What changed:",
    ]
    lines.extend(f"• {update}" for update in visible)
    if len(updates) > len(visible):
        lines.append(
            f"• System: {len(updates) - len(visible)} additional validated maintenance updates were included."
        )
    lines.extend(
        (
            "",
            "Status: The updated services passed the deployment checks and are now running.",
        )
    )
    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:3960].rsplit("\n", 1)[0] + "\n• Additional validated updates were included."
    return message


def shell_assignment(name: str, value: str) -> str:
    return f"{name}={shlex.quote(value)}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a non-sensitive Telegram summary for one deployment."
    )
    parser.add_argument("--from-commit", default="")
    parser.add_argument("--to-commit", default="HEAD")
    parser.add_argument("--shell", action="store_true")
    args = parser.parse_args()

    current = git("rev-parse", args.to_commit)
    previous = args.from_commit.strip()
    if not valid_commit(previous):
        previous = git("rev-parse", f"{current}^", check=False)
    subjects = release_subjects(previous, current)
    message = build_message(subjects)
    encoded = base64.b64encode(message.encode("utf-8")).decode("ascii") if message else ""

    if args.shell:
        print(shell_assignment("DEPLOYMENT_RELEASE_ID", current))
        print(shell_assignment("DEPLOYMENT_RELEASE_FROM", previous))
        print(shell_assignment("DEPLOYMENT_RELEASE_CHANGE_COUNT", str(len(subjects))))
        print(shell_assignment("DEPLOYMENT_RELEASE_MESSAGE_B64", encoded))
    else:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
