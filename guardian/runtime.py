from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .config import GuardianConfig
from .security import redact, safe_repo_path

LOGGER = logging.getLogger("legacy_model.guardian.runtime")

_ERROR_RE = re.compile(
    r"(?i)(traceback|exception|error|failed|unhealthy|critical|database_unavailable|"
    r"purchase_failed|oauth.*failed|connection refused|timed out|restart loop|"
    r"syntaxerror|importerror|assertionerror|integrityerror)"
)
_NOISE_RE = re.compile(
    r"(?i)(tick received|heartbeat|candidate.*skip|cadence_gate|prefilter|"
    r"telegram_notifications_suspended)"
)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


class GuardianRuntime:
    def __init__(self, config: GuardianConfig) -> None:
        self.config = config

    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 120,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            cwd=str(cwd or self.config.repo_dir),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"Command failed ({result.returncode}): {' '.join(command)}\n"
                + redact(result.stdout[-8000:])
            )
        return result

    def compose(
        self,
        *arguments: str,
        timeout: int = 120,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = ["docker", "compose"]
        for filename in self.config.compose_files:
            command.extend(("-f", filename))
        command.extend(arguments)
        return self.run(command, timeout=timeout, check=check)

    def current_commit(self, *, cwd: Path | None = None) -> str:
        return self.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, timeout=30
        ).stdout.strip()

    @staticmethod
    def _service_record(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": item.get("Service") or item.get("Name"),
            "state": item.get("State"),
            "health": item.get("Health"),
            "status": item.get("Status"),
        }

    @classmethod
    def _parse_compose_ps(cls, raw: str) -> list[dict[str, Any]]:
        text = str(raw or "").strip()
        if not text:
            return []
        try:
            value = json.loads(text)
            if isinstance(value, list):
                return [
                    cls._service_record(item)
                    for item in value
                    if isinstance(item, dict)
                ]
            if isinstance(value, dict):
                return [cls._service_record(value)]
        except ValueError:
            pass

        services: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                services.append(cls._service_record(item))
        return services

    def health_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "http": {},
            "services": [],
            "git": {},
        }
        try:
            response = requests.get(self.config.health_url, timeout=8)
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text[:1000]
            snapshot["http"] = {
                "url": self.config.health_url,
                "status_code": response.status_code,
                "ok": response.ok,
                "body": body,
            }
        except requests.RequestException as exc:
            snapshot["http"] = {
                "url": self.config.health_url,
                "status_code": 0,
                "ok": False,
                "error": type(exc).__name__,
            }

        result = self.compose("ps", "--format", "json", timeout=30, check=False)
        snapshot["services"] = self._parse_compose_ps(result.stdout)

        git_status = self.run(
            ["git", "status", "--short"], timeout=30, check=False
        ).stdout.strip()
        snapshot["git"] = {
            "commit": self.current_commit(),
            "clean": not bool(git_status),
            "status": redact(git_status[:2000]),
        }
        return snapshot

    def collect_logs(self) -> str:
        result = self.compose(
            "logs",
            "--no-color",
            f"--since={self.config.log_lookback_seconds}s",
            "database",
            "api",
            "worker",
            timeout=60,
            check=False,
        )
        lines = result.stdout.splitlines()
        selected = [
            line
            for line in lines
            if _ERROR_RE.search(line) and not _NOISE_RE.search(line)
        ]
        if not selected:
            return ""
        return redact("\n".join(selected[-self.config.maximum_log_lines :]))

    @staticmethod
    def incident_fingerprint(evidence: str, health: dict[str, Any]) -> str:
        normalized_lines: list[str] = []
        for line in str(evidence).splitlines()[-80:]:
            value = re.sub(r"\d+", "#", line.lower())
            value = re.sub(r"0x[0-9a-f]+", "0x#", value)
            value = re.sub(r"\s+", " ", value).strip()
            normalized_lines.append(value[-500:])
        health_key = json.dumps(
            {
                "http_ok": (health.get("http") or {}).get("ok"),
                "services": health.get("services") or [],
            },
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(
            ("\n".join(normalized_lines) + "\n" + health_key).encode("utf-8")
        ).hexdigest()
        return digest[:32]

    @staticmethod
    def unhealthy(snapshot: dict[str, Any]) -> bool:
        if not bool((snapshot.get("http") or {}).get("ok")):
            return True
        services = snapshot.get("services") or []
        expected = {"database", "api", "worker"}
        present = {str(item.get("service") or "") for item in services}
        if not expected.issubset(present):
            return True
        for item in services:
            if str(item.get("state") or "").lower() != "running":
                return True
            health = str(item.get("health") or "").lower()
            if health and health not in {"healthy", "starting"}:
                return True
        return False

    def repository_context(self, evidence: str) -> str:
        terms: list[str] = []
        seen: set[str] = set()
        for token in _TOKEN_RE.findall(evidence):
            lowered = token.lower()
            if lowered in seen or len(lowered) < 5:
                continue
            if lowered in {
                "error",
                "failed",
                "exception",
                "traceback",
                "worker",
                "database",
            }:
                continue
            seen.add(lowered)
            terms.append(token)
            if len(terms) >= 10:
                break

        sections = [
            "CURRENT COMMIT:\n" + self.current_commit(),
            "RECENT COMMITS:\n"
            + self.run(
                ["git", "log", "--oneline", "-12"], timeout=30, check=False
            ).stdout[:4000],
        ]
        if terms:
            pattern = "|".join(re.escape(term) for term in terms)
            grep = self.run(
                [
                    "git",
                    "grep",
                    "-n",
                    "-I",
                    "-E",
                    pattern,
                    "--",
                    "app",
                    "scripts",
                    "guardian",
                ],
                timeout=45,
                check=False,
            ).stdout
            sections.append("RELEVANT CODE SEARCH:\n" + redact(grep[:25_000]))
        return "\n\n".join(sections)

    def load_patch_context(self, candidate_paths: Iterable[str]) -> str:
        blocks: list[str] = []
        total = 0
        for raw in list(candidate_paths)[:10]:
            try:
                path = safe_repo_path(self.config.repo_dir, str(raw))
            except ValueError:
                continue
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > 70_000:
                content = (
                    content[:35_000]
                    + "\n... [TRUNCATED] ...\n"
                    + content[-20_000:]
                )
            block = (
                f"===== FILE: {path.relative_to(self.config.repo_dir)} =====\n"
                + content
            )
            if total + len(block) > 180_000:
                break
            blocks.append(block)
            total += len(block)
        return redact("\n\n".join(blocks), maximum_chars=180_000)

    @staticmethod
    def _with_mode(url: str, mode: str) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["mode"] = mode
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    @staticmethod
    def _json_get(url: str) -> dict[str, Any]:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"value": payload}

    def metrics_snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "demo": {},
            "real": {},
        }
        errors: list[dict[str, str]] = []
        for mode in ("demo", "real"):
            url = self._with_mode(self.config.metrics_url, mode)
            try:
                result[mode] = self._json_get(url)
            except (requests.RequestException, ValueError) as exc:
                result[mode] = {"unavailable": True, "error": type(exc).__name__}
                errors.append({"mode": mode, "error": type(exc).__name__})
        if errors:
            result["errors"] = errors
        result["unavailable"] = all(
            bool((result.get(mode) or {}).get("unavailable"))
            for mode in ("demo", "real")
        )
        return result
