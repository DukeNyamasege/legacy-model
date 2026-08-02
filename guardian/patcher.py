from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import GuardianConfig
from .openai_client import GuardianOpenAI
from .runtime import GuardianRuntime
from .security import redact, safe_repo_path, sanitize_test_command, validate_diff

LOGGER = logging.getLogger("legacy_model.guardian.patcher")


class GuardianPatcher:
    def __init__(
        self,
        config: GuardianConfig,
        runtime: GuardianRuntime,
        ai: GuardianOpenAI,
    ) -> None:
        self.config = config
        self.runtime = runtime
        self.ai = ai

    def _run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout: int = 300,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            cwd=str(cwd),
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
                + redact(result.stdout[-12_000:])
            )
        return result

    def _assert_live_checkout_clean(self) -> None:
        status = self._run(
            ["git", "status", "--porcelain"],
            cwd=self.config.repo_dir,
            timeout=30,
        ).stdout.strip()
        if status:
            raise RuntimeError(
                "Live checkout contains uncommitted changes. Guardian refused to create or "
                "push a competing patch:\n" + redact(status)
            )

    def _worktree(self, incident_id: int, base_commit: str) -> tuple[Path, str]:
        self._assert_live_checkout_clean()
        if len(base_commit) != 40:
            raise RuntimeError("Incident has no valid 40-character base commit")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        branch = f"guardian/incident-{int(incident_id)}-{stamp}"
        path = self.config.worktree_root / f"incident-{int(incident_id)}-{stamp}"
        self._run(
            ["git", "fetch", "origin", "main"],
            cwd=self.config.repo_dir,
            timeout=120,
        )
        origin_main = self._run(
            ["git", "rev-parse", "origin/main"],
            cwd=self.config.repo_dir,
            timeout=30,
        ).stdout.strip()
        if origin_main != base_commit:
            raise RuntimeError(
                "Main moved after the incident was diagnosed. A new diagnosis is required "
                f"before editing. diagnosed={base_commit[:12]} current={origin_main[:12]}"
            )
        self._run(
            ["git", "worktree", "add", "-b", branch, str(path), origin_main],
            cwd=self.config.repo_dir,
            timeout=120,
        )
        return path, branch

    def _write_files(self, worktree: Path, patch: dict[str, Any]) -> list[str]:
        files = patch.get("files") or []
        if not isinstance(files, list) or not files:
            raise ValueError("Coding model returned no files")
        if len(files) > 10:
            raise ValueError("Coding model attempted to modify more than 10 files")

        changed: list[str] = []
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Invalid file replacement object")
            relative = str(item.get("path") or "").strip().replace("\\", "/")
            content = item.get("content")
            if relative in seen:
                raise ValueError(f"Coding model returned the same file twice: {relative}")
            seen.add(relative)
            if not isinstance(content, str):
                raise ValueError(f"File content is not text: {relative}")
            if len(content) > 250_000:
                raise ValueError(f"File replacement is too large: {relative}")
            destination = safe_repo_path(worktree, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            changed.append(relative)
        return changed

    def _test_commands(self, patch: dict[str, Any]) -> list[str]:
        commands = [
            "python -m compileall -q app scripts guardian",
            "python -m unittest -q guardian.tests.test_guardian",
        ]
        for raw in patch.get("tests") or []:
            try:
                command = sanitize_test_command(str(raw))
            except ValueError:
                continue
            if command not in commands:
                commands.append(command)
        return commands[:8]

    def _run_tests(self, worktree: Path, commands: list[str]) -> str:
        outputs: list[str] = []
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        venv_bin = str(Path(sys.executable).resolve().parent)
        environment["PATH"] = venv_bin + os.pathsep + environment.get("PATH", "")
        for command in commands:
            arguments = shlex.split(command)
            result = self._run(
                arguments,
                cwd=worktree,
                timeout=600,
                check=False,
                env=environment,
            )
            outputs.append(
                f"$ {command}\nexit={result.returncode}\n"
                f"{redact(result.stdout[-20_000:])}"
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "Guardian validation failed:\n" + "\n\n".join(outputs)
                )
        return "\n\n".join(outputs)

    def _run_deploy(self, previous_commit: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["DEPLOY_PREVIOUS_COMMIT"] = previous_commit
        return self._run(
            ["sh", "./scripts/deploy_vps.sh"],
            cwd=self.config.repo_dir,
            timeout=self.config.deployment_timeout_seconds,
            check=False,
            env=environment,
        )

    def _rollback_failed_deployment(
        self,
        *,
        previous_commit: str,
        failed_commit: str,
        failed_output: str,
    ) -> str:
        details = [
            "Approved commit deployment failed.",
            redact(failed_output[-18_000:]),
        ]
        try:
            self._assert_live_checkout_clean()
            self._run(
                ["git", "fetch", "origin", "main"],
                cwd=self.config.repo_dir,
                timeout=120,
            )
            remote = self._run(
                ["git", "rev-parse", "origin/main"],
                cwd=self.config.repo_dir,
                timeout=30,
            ).stdout.strip()
            if remote != failed_commit:
                raise RuntimeError(
                    "origin/main moved after the failed deployment; automatic revert was refused"
                )
            self._run(
                ["git", "revert", "--no-edit", failed_commit],
                cwd=self.config.repo_dir,
                timeout=180,
            )
            revert_commit = self.runtime.current_commit()
            self._run(
                ["git", "push", "origin", "HEAD:main"],
                cwd=self.config.repo_dir,
                timeout=180,
            )
            rollback = self._run_deploy(failed_commit)
            if rollback.returncode != 0:
                raise RuntimeError(
                    "revert commit was pushed, but rollback deployment failed:\n"
                    + redact(rollback.stdout[-18_000:])
                )
            details.extend(
                (
                    "Automatic rollback succeeded.",
                    f"Failed commit: {failed_commit}",
                    f"Restored base: {previous_commit}",
                    f"Revert commit: {revert_commit}",
                )
            )
        except Exception as exc:
            details.extend(
                (
                    "Automatic rollback did not complete.",
                    redact(str(exc), maximum_chars=5000),
                )
            )
        return "\n".join(details)

    def _deploy(self, previous_commit: str, new_commit: str) -> str:
        if not self.config.auto_deploy:
            return "Automatic deployment is disabled."
        self._assert_live_checkout_clean()
        self._run(
            ["git", "fetch", "origin", "main"],
            cwd=self.config.repo_dir,
            timeout=120,
        )
        self._run(
            ["git", "merge", "--ff-only", "origin/main"],
            cwd=self.config.repo_dir,
            timeout=120,
        )
        current = self.runtime.current_commit()
        if current != new_commit:
            raise RuntimeError(
                f"Live checkout did not reach approved commit {new_commit[:12]}"
            )
        result = self._run_deploy(previous_commit)
        if result.returncode != 0:
            rollback_report = self._rollback_failed_deployment(
                previous_commit=previous_commit,
                failed_commit=new_commit,
                failed_output=result.stdout,
            )
            raise RuntimeError(rollback_report)
        return redact(result.stdout[-30_000:])

    def apply(self, incident: dict[str, Any]) -> dict[str, Any]:
        analysis = incident.get("analysis") or {}
        category = str(
            analysis.get("category") or incident.get("category") or ""
        ).lower()
        if category in {"strategy", "performance"}:
            raise RuntimeError(
                "Strategy and performance advice cannot be auto-applied by the Guardian"
            )
        if not bool(analysis.get("needs_code_change")):
            return {
                "status": "acknowledged",
                "message": "No repository change was required.",
            }
        if self.config.dry_run:
            return {
                "status": "dry_run",
                "message": "Approval received, but GUARDIAN_DRY_RUN=true prevented changes.",
            }

        base_commit = str(incident.get("base_commit") or "")
        worktree: Path | None = None
        branch = ""
        try:
            worktree, branch = self._worktree(int(incident["id"]), base_commit)
            candidate_paths = [
                str(path) for path in analysis.get("candidate_paths") or []
            ]
            context = self.runtime.load_patch_context(candidate_paths)
            if not context.strip():
                raise RuntimeError(
                    "No safe repository files were resolved from the diagnosis. "
                    "The incident requires manual review."
                )
            patch = self.ai.create_patch(
                incident=incident,
                repository_context=context,
            )
            changed = self._write_files(worktree, patch)

            # Intent-to-add makes new files visible to git diff without staging
            # their actual content before policy validation and review.
            self._run(
                ["git", "add", "-N", "--", *changed],
                cwd=worktree,
                timeout=60,
            )
            diff = self._run(
                ["git", "diff", "--", *changed],
                cwd=worktree,
                timeout=60,
            ).stdout
            if not diff.strip():
                raise RuntimeError("The proposed replacement produced no Git diff")
            validate_diff(diff)

            tests = self._test_commands(patch)
            test_output = self._run_tests(worktree, tests)
            review = self.ai.review_patch(
                incident=incident,
                diff_text=redact(diff, maximum_chars=140_000),
                test_output=test_output,
            )
            if not bool(review.get("approved")):
                raise RuntimeError(
                    "Independent AI review rejected the patch: "
                    + str(
                        review.get("summary")
                        or review.get("required_changes")
                        or "unknown"
                    )
                )

            self._run(
                ["git", "add", "--", *changed],
                cwd=worktree,
                timeout=60,
            )
            commit_message = str(
                patch.get("commit_message") or "Guardian approved production fix"
            )[:120]
            self._run(
                ["git", "commit", "-m", commit_message],
                cwd=worktree,
                timeout=120,
            )
            new_commit = self._run(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree,
                timeout=30,
            ).stdout.strip()

            self._run(
                ["git", "fetch", "origin", "main"],
                cwd=worktree,
                timeout=120,
            )
            origin_main = self._run(
                ["git", "rev-parse", "origin/main"],
                cwd=worktree,
                timeout=30,
            ).stdout.strip()
            if origin_main != base_commit:
                raise RuntimeError(
                    "Main changed while the approved patch was being tested. Push was refused."
                )
            if not self.config.allow_main_push:
                raise RuntimeError(
                    "GUARDIAN_ALLOW_MAIN_PUSH=false; push was refused"
                )
            self._run(
                ["git", "push", "origin", "HEAD:main"],
                cwd=worktree,
                timeout=180,
            )
            deployment = self._deploy(base_commit, new_commit)
            return {
                "status": "completed",
                "base_commit": base_commit,
                "commit": new_commit,
                "branch": branch,
                "changed_files": changed,
                "tests": tests,
                "review": review,
                "deployment_tail": deployment[-12_000:],
                "summary": str(
                    patch.get("summary") or "Approved fix applied."
                ),
            }
        finally:
            if worktree is not None and worktree.exists():
                self._run(
                    ["git", "worktree", "remove", "--force", str(worktree)],
                    cwd=self.config.repo_dir,
                    timeout=120,
                    check=False,
                )
                shutil.rmtree(worktree, ignore_errors=True)
            if branch:
                self._run(
                    ["git", "branch", "-D", branch],
                    cwd=self.config.repo_dir,
                    timeout=60,
                    check=False,
                )
