from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import GuardianConfig
from .openai_client import GuardianOpenAI
from .runtime import GuardianRuntime
from .sandbox import GuardianSandbox
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
        self.sandbox = GuardianSandbox(config, runtime)

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

    def _patch_scope(
        self,
        candidate_paths: list[str],
    ) -> tuple[set[str], set[str]]:
        exact: set[str] = set()
        new_file_parents: set[str] = set()
        for raw in candidate_paths[:10]:
            try:
                path = safe_repo_path(self.config.repo_dir, raw)
            except ValueError:
                continue
            relative = path.relative_to(self.config.repo_dir).as_posix()
            exact.add(relative)
            if path.is_file():
                parent = Path(relative).parent.as_posix()
                if parent not in {"", "."}:
                    new_file_parents.add(parent)
        return exact, new_file_parents

    def _write_files(
        self,
        worktree: Path,
        patch: dict[str, Any],
        *,
        exact_scope: set[str],
        new_file_parents: set[str],
    ) -> list[str]:
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
            destination = safe_repo_path(worktree, relative)
            normalized = destination.relative_to(worktree).as_posix()
            if normalized in seen:
                raise ValueError(
                    f"Coding model returned the same file twice: {normalized}"
                )
            seen.add(normalized)
            if not isinstance(content, str):
                raise ValueError(f"File content is not text: {normalized}")
            if len(content) > 250_000:
                raise ValueError(f"File replacement is too large: {normalized}")

            live_path = self.config.repo_dir / normalized
            is_new = not live_path.exists()
            parent = Path(normalized).parent.as_posix()
            new_test = is_new and (
                normalized.startswith("tests/")
                or normalized.startswith("guardian/tests/")
            )
            allowed_new_helper = (
                is_new
                and parent not in {"", "."}
                and parent in new_file_parents
            )
            if normalized not in exact_scope and not new_test and not allowed_new_helper:
                raise ValueError(
                    "Coding model attempted to modify a file outside the diagnosed scope: "
                    + normalized
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            changed.append(normalized)
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
        for command in commands:
            returncode, output = self.sandbox.run(
                worktree=worktree,
                command=command,
                timeout=600,
            )
            outputs.append(f"$ {command}\nexit={returncode}\n{output}")
            if returncode != 0:
                raise RuntimeError(
                    "Guardian sandbox validation failed:\n" + "\n\n".join(outputs)
                )
        return "\n\n".join(outputs)

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
            exact_scope, new_file_parents = self._patch_scope(candidate_paths)
            context = self.runtime.load_patch_context(candidate_paths)
            if not context.strip() or not exact_scope:
                raise RuntimeError(
                    "No safe repository files were resolved from the diagnosis. "
                    "The incident requires manual review."
                )
            patch = self.ai.create_patch(
                incident=incident,
                repository_context=context,
            )
            changed = self._write_files(
                worktree,
                patch,
                exact_scope=exact_scope,
                new_file_parents=new_file_parents,
            )

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
            return {
                "status": "completed",
                "base_commit": base_commit,
                "commit": new_commit,
                "branch": branch,
                "changed_files": changed,
                "tests": tests,
                "review": review,
                "repository_update": "pushed_to_main",
                "vps_update": "not_performed",
                "next_action": (
                    "Review the Git commit and update the VPS manually when the full "
                    "release is ready."
                ),
                "summary": str(
                    patch.get("summary") or "Approved fix pushed to Git main."
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
