from __future__ import annotations

import logging
import os
import shlex
import stat
import subprocess
import uuid
from pathlib import Path

from .config import GuardianConfig
from .runtime import GuardianRuntime
from .security import redact

LOGGER = logging.getLogger("legacy_model.guardian.sandbox")


class GuardianSandbox:
    """Execute untrusted generated tests without host secrets or write access."""

    def __init__(self, config: GuardianConfig, runtime: GuardianRuntime) -> None:
        self.config = config
        self.runtime = runtime

    def _worker_image(self) -> str:
        result = self.runtime.compose(
            "images",
            "-q",
            "worker",
            timeout=60,
            check=False,
        )
        images = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not images:
            raise RuntimeError(
                "No local worker image is available for Guardian sandbox tests. "
                "Deploy the current production stack before approving fixes."
            )
        return images[0]

    @staticmethod
    def _make_source_readable(worktree: Path) -> None:
        """Permit the non-root container user to read only this temporary checkout."""

        for root, directories, files in os.walk(worktree, followlinks=False):
            root_path = Path(root)
            root_path.chmod(0o755)
            for directory in directories:
                path = root_path / directory
                if not path.is_symlink():
                    path.chmod(0o755)
            for filename in files:
                path = root_path / filename
                if path.is_symlink():
                    continue
                mode = path.stat().st_mode
                path.chmod(0o755 if mode & stat.S_IXUSR else 0o644)

    def run(self, *, worktree: Path, command: str, timeout: int = 600) -> tuple[int, str]:
        arguments = shlex.split(str(command or ""))
        if not arguments:
            raise ValueError("Sandbox test command is empty")

        self._make_source_readable(worktree)
        name = f"legacy-guardian-test-{uuid.uuid4().hex[:12]}"
        image = self._worker_image()
        docker_command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "128",
            "--memory",
            "1024m",
            "--cpus",
            "1.0",
            "--ulimit",
            "nofile=256:256",
            "--user",
            "10001:10001",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777",
            "--tmpfs",
            "/workspace:rw,nosuid,nodev,size=512m,mode=0755,uid=10001,gid=10001",
            "--mount",
            f"type=bind,src={worktree.resolve()},dst=/source,readonly",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTEST_ADDOPTS=-p no:cacheprovider",
            image,
            "sh",
            "-ec",
            'cp -R /source/. /workspace/ && cd /workspace && exec "$@"',
            "guardian-sandbox",
            *arguments,
        ]
        try:
            result = subprocess.run(
                docker_command,
                cwd=str(self.config.repo_dir),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            return result.returncode, redact(result.stdout[-30_000:])
        except subprocess.TimeoutExpired as exc:
            subprocess.run(
                ["docker", "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            output = (
                exc.stdout.decode()
                if isinstance(exc.stdout, bytes)
                else str(exc.stdout or "")
            )
            raise RuntimeError(
                f"Sandbox test timed out after {timeout} seconds:\n"
                + redact(output[-10_000:])
            ) from exc
