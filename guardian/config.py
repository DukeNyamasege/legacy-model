from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class GuardianConfig:
    repo_dir: Path
    state_dir: Path
    compose_files: tuple[str, ...]
    openai_api_key: str
    diagnosis_model: str
    coding_model: str
    reviewer_model: str
    telegram_bot_token: str
    telegram_admin_chat_id: str
    scan_interval_seconds: int
    strategy_review_interval_seconds: int
    log_lookback_seconds: int
    maximum_log_lines: int
    auto_deploy: bool
    deployment_timeout_seconds: int
    health_url: str
    metrics_url: str
    allow_main_push: bool
    dry_run: bool

    @property
    def database_path(self) -> Path:
        return self.state_dir / "guardian.sqlite3"

    @property
    def worktree_root(self) -> Path:
        return self.state_dir / "worktrees"

    @classmethod
    def from_env(cls) -> "GuardianConfig":
        repo_dir = Path(os.getenv("GUARDIAN_REPO_DIR", "/root/legacy-model")).resolve()
        state_dir = Path(
            os.getenv("GUARDIAN_STATE_DIR", "/var/lib/legacy-model-guardian")
        ).resolve()
        compose_files = tuple(
            item.strip()
            for item in os.getenv(
                "GUARDIAN_COMPOSE_FILES",
                "docker-compose.yml,docker-compose.vps.yml",
            ).split(",")
            if item.strip()
        )
        return cls(
            repo_dir=repo_dir,
            state_dir=state_dir,
            compose_files=compose_files,
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            diagnosis_model=os.getenv(
                "GUARDIAN_DIAGNOSIS_MODEL", "gpt-5.4-mini"
            ).strip(),
            coding_model=os.getenv(
                "GUARDIAN_CODING_MODEL", "gpt-5.3-codex"
            ).strip(),
            reviewer_model=os.getenv(
                "GUARDIAN_REVIEWER_MODEL", "gpt-5.4-mini"
            ).strip(),
            telegram_bot_token=os.getenv(
                "GUARDIAN_TELEGRAM_BOT_TOKEN", ""
            ).strip(),
            telegram_admin_chat_id=os.getenv(
                "GUARDIAN_TELEGRAM_ADMIN_CHAT_ID", ""
            ).strip(),
            scan_interval_seconds=_int("GUARDIAN_SCAN_INTERVAL_SECONDS", 20),
            strategy_review_interval_seconds=_int(
                "GUARDIAN_STRATEGY_REVIEW_INTERVAL_SECONDS",
                21600,
                minimum=300,
            ),
            log_lookback_seconds=_int("GUARDIAN_LOG_LOOKBACK_SECONDS", 90),
            maximum_log_lines=_int("GUARDIAN_MAXIMUM_LOG_LINES", 240),
            auto_deploy=_bool("GUARDIAN_AUTO_DEPLOY", True),
            deployment_timeout_seconds=_int(
                "GUARDIAN_DEPLOYMENT_TIMEOUT_SECONDS",
                1200,
                minimum=60,
            ),
            health_url=os.getenv(
                "GUARDIAN_HEALTH_URL",
                "http://127.0.0.1:8080/health/ready",
            ).strip(),
            metrics_url=os.getenv(
                "GUARDIAN_METRICS_URL",
                "http://127.0.0.1:8080/metrics/summary?mode=demo",
            ).strip(),
            allow_main_push=_bool("GUARDIAN_ALLOW_MAIN_PUSH", True),
            dry_run=_bool("GUARDIAN_DRY_RUN", False),
        )

    def validate(self) -> None:
        missing: list[str] = []
        if not self.repo_dir.is_dir() or not (self.repo_dir / ".git").exists():
            missing.append(f"Git repository directory {self.repo_dir}")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.diagnosis_model:
            missing.append("GUARDIAN_DIAGNOSIS_MODEL")
        if not self.coding_model:
            missing.append("GUARDIAN_CODING_MODEL")
        if not self.reviewer_model:
            missing.append("GUARDIAN_REVIEWER_MODEL")
        if not self.telegram_bot_token:
            missing.append("GUARDIAN_TELEGRAM_BOT_TOKEN")
        if not self.telegram_admin_chat_id:
            missing.append("GUARDIAN_TELEGRAM_ADMIN_CHAT_ID")
        else:
            try:
                chat_id = int(self.telegram_admin_chat_id)
            except ValueError:
                chat_id = 0
            if chat_id <= 0:
                missing.append(
                    "a positive private GUARDIAN_TELEGRAM_ADMIN_CHAT_ID"
                )
        if not self.compose_files:
            missing.append("GUARDIAN_COMPOSE_FILES")
        else:
            for filename in self.compose_files:
                if not (self.repo_dir / filename).is_file():
                    missing.append(f"compose file {filename}")
        if missing:
            raise RuntimeError(
                "Guardian configuration is incomplete: " + ", ".join(missing)
            )

        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.worktree_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.worktree_root.chmod(0o700)
