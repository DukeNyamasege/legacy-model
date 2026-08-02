from __future__ import annotations

import json
import logging
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .config import GuardianConfig
from .openai_client import GuardianOpenAI
from .patcher import GuardianPatcher
from .runtime import GuardianRuntime
from .security import redact
from .store import GuardianStore
from .telegram import GuardianTelegram

LOGGER = logging.getLogger("legacy_model.guardian")


class GuardianService:
    def __init__(self, config: GuardianConfig) -> None:
        config.validate()
        self.config = config
        self.store = GuardianStore(config.database_path)
        self.runtime = GuardianRuntime(config)
        self.ai = GuardianOpenAI(config)
        self.telegram = GuardianTelegram(config)
        self.patcher = GuardianPatcher(config, self.runtime, self.ai)
        self.running = threading.Event()
        self.running.set()
        self.patch_lock = threading.Lock()
        self.last_strategy_review = 0.0

    def stop(self, *_args: Any) -> None:
        self.running.clear()

    def _telegram_loop(self) -> None:
        while self.running.is_set():
            try:
                self.telegram.poll(self._handle_telegram_action)
            except Exception as exc:
                LOGGER.warning(
                    "GUARDIAN_TELEGRAM_POLL_FAILED error=%s",
                    type(exc).__name__,
                )
                time.sleep(5)

    def _status_text(self) -> str:
        snapshot = self.runtime.health_snapshot()
        http = snapshot.get("http") or {}
        lines = [
            "🛡 LEGACY MODEL GUARDIAN STATUS",
            "",
            f"Guardian: {'RUNNING' if self.running.is_set() else 'STOPPING'}",
            f"Repository: {self.config.repo_dir}",
            f"Commit: {str((snapshot.get('git') or {}).get('commit') or '')[:12]}",
            f"Repository clean: {(snapshot.get('git') or {}).get('clean')}",
            f"API ready: {http.get('ok')} ({http.get('status_code')})",
            f"Automatic deployment: {self.config.auto_deploy}",
            f"Main push enabled: {self.config.allow_main_push}",
            f"Dry run: {self.config.dry_run}",
            "",
            "Services:",
        ]
        for item in snapshot.get("services") or []:
            lines.append(
                f"• {item.get('service')}: {item.get('state')} / "
                f"{item.get('health') or item.get('status') or 'unknown'}"
            )
        return "\n".join(lines)

    def _handle_telegram_action(
        self,
        data: str,
        message_id: int,
        _update_id: int,
        callback_id: str,
    ) -> None:
        parts = str(data or "").split(":")
        if len(parts) != 3 or parts[0] != "guardian":
            return
        action = parts[1]
        try:
            incident_id = int(parts[2])
        except ValueError:
            return

        if action == "status":
            self.telegram.send_text(self._status_text())
            return

        incident = self.store.incident(incident_id)
        if incident is None:
            self.telegram.answer_callback(callback_id, "Incident was not found")
            return

        if action == "details":
            analysis = incident.get("analysis") or {}
            text = "\n".join(
                (
                    f"📋 GUARDIAN INCIDENT #{incident_id}",
                    "",
                    f"Status: {incident.get('status')}",
                    f"Category: {incident.get('category')}",
                    f"Severity: {incident.get('severity')}",
                    f"Base commit: {str(incident.get('base_commit') or '')[:12]}",
                    "",
                    "Analysis:",
                    json.dumps(analysis, indent=2, default=str)[:2600],
                    "",
                    "Evidence:",
                    str(incident.get("evidence") or "")[-1000:],
                )
            )
            self.telegram.send_text(text)
            self.telegram.answer_callback(callback_id, "Details sent privately")
            return

        if action == "reject":
            changed = self.store.transition(
                incident_id,
                expected=("proposed", "approved"),
                target="rejected",
                approved_by=self.config.telegram_admin_chat_id,
                result={"reason": "Rejected from Telegram"},
            )
            self.telegram.answer_callback(
                callback_id,
                "Rejected" if changed else "Incident is no longer awaiting a decision",
            )
            if changed and message_id:
                self.telegram.edit_status(
                    message_id,
                    f"❌ Guardian incident #{incident_id} rejected. No code or deployment action was taken.",
                )
            return

        if action != "approve":
            return

        analysis = incident.get("analysis") or {}
        needs_code = bool(analysis.get("needs_code_change"))
        category = str(analysis.get("category") or incident.get("category") or "")
        if not needs_code or category in {"strategy", "performance"}:
            changed = self.store.transition(
                incident_id,
                expected=("proposed",),
                target="acknowledged",
                approved_by=self.config.telegram_admin_chat_id,
                result={"message": "Advice acknowledged; no automatic code change allowed."},
            )
            self.telegram.answer_callback(
                callback_id,
                "Acknowledged" if changed else "Already handled",
            )
            if changed and message_id:
                self.telegram.edit_status(
                    message_id,
                    f"✅ Guardian advice #{incident_id} acknowledged. Strategy and performance advice was not auto-applied.",
                )
            return

        changed = self.store.transition(
            incident_id,
            expected=("proposed",),
            target="approved",
            approved_by=self.config.telegram_admin_chat_id,
            result={"message": "Approved from private Telegram chat"},
        )
        self.telegram.answer_callback(
            callback_id,
            "Approved. Isolated patching and tests are starting."
            if changed
            else "Incident is no longer awaiting approval",
        )
        if not changed:
            return
        if message_id:
            self.telegram.edit_status(
                message_id,
                f"⏳ Guardian incident #{incident_id} approved. Creating an isolated patch, running tests and independent review.",
            )
        thread = threading.Thread(
            target=self._apply_incident,
            args=(incident_id, message_id),
            daemon=True,
            name=f"guardian-remediation-{incident_id}",
        )
        thread.start()

    def _apply_incident(self, incident_id: int, message_id: int) -> None:
        if not self.patch_lock.acquire(blocking=False):
            self.store.transition(
                incident_id,
                expected=("approved",),
                target="failed",
                result={"error": "Another approved remediation is already running"},
            )
            self.telegram.send_text(
                f"❌ Guardian incident #{incident_id} was not started because another remediation is already running."
            )
            return
        try:
            if not self.store.transition(
                incident_id,
                expected=("approved",),
                target="working",
                result={"started_at": datetime.now(timezone.utc).isoformat()},
            ):
                return
            incident = self.store.incident(incident_id)
            if incident is None:
                return
            result = self.patcher.apply(incident)
            self.store.transition(
                incident_id,
                expected=("working",),
                target="completed",
                result=result,
            )
            commit = str(result.get("commit") or "")
            text = "\n".join(
                (
                    f"✅ GUARDIAN FIX COMPLETED — INCIDENT #{incident_id}",
                    "",
                    str(result.get("summary") or result.get("message") or "Approved action completed."),
                    f"Commit: {commit[:12] if commit else 'No code commit required'}",
                    "Changed files: " + ", ".join(result.get("changed_files") or [])[:1200],
                    "Tests: " + "; ".join(result.get("tests") or [])[:1200],
                    f"Deployment: {'completed' if result.get('deployment_tail') else 'not requested'}",
                )
            )
            if message_id:
                self.telegram.edit_status(message_id, text)
            else:
                self.telegram.send_text(text)
        except Exception as exc:
            error = redact(str(exc), maximum_chars=3500)
            self.store.transition(
                incident_id,
                expected=("working", "approved"),
                target="failed",
                result={"error": error},
            )
            text = "\n".join(
                (
                    f"❌ GUARDIAN FIX FAILED SAFELY — INCIDENT #{incident_id}",
                    "",
                    error,
                    "",
                    "No force-push or database/volume deletion was performed.",
                )
            )
            if message_id:
                self.telegram.edit_status(message_id, text)
            else:
                self.telegram.send_text(text)
            LOGGER.exception("GUARDIAN_REMEDIATION_FAILED incident_id=%s", incident_id)
        finally:
            self.patch_lock.release()

    def _incident_cooldown_active(self, fingerprint: str) -> bool:
        key = f"incident_seen:{fingerprint}"
        raw = self.store.get(key)
        try:
            last = float(raw)
        except ValueError:
            last = 0.0
        now = time.time()
        if now - last < 1800:
            return True
        self.store.set(key, str(now))
        return False

    def _scan(self) -> None:
        health = self.runtime.health_snapshot()
        evidence = self.runtime.collect_logs()
        unhealthy = self.runtime.unhealthy(health)
        if not unhealthy and not evidence:
            self.store.set("last_healthy_at", datetime.now(timezone.utc).isoformat())
            return
        if not evidence:
            evidence = "Health check reported an unhealthy or missing service."

        fingerprint = self.runtime.incident_fingerprint(evidence, health)
        if self._incident_cooldown_active(fingerprint):
            return
        repository_context = self.runtime.repository_context(evidence)
        analysis = self.ai.diagnose(
            evidence=evidence,
            health=health,
            repository_context=repository_context,
        )
        incident_id = self.store.create_incident(
            fingerprint=fingerprint,
            category=str(analysis.get("category") or "error"),
            severity=str(analysis.get("severity") or "warning"),
            title=str(analysis.get("title") or "Guardian incident")[:300],
            summary=str(analysis.get("summary") or "")[:4000],
            evidence=redact(evidence, maximum_chars=40_000),
            analysis=analysis,
            base_commit=str((health.get("git") or {}).get("commit") or ""),
        )
        if incident_id is None:
            return
        message_id = self.telegram.send_incident(incident_id, analysis, evidence)
        self.store.set_message_id(incident_id, message_id)
        self.store.add_event(
            "incident_proposed",
            {"fingerprint": fingerprint, "message_id": message_id},
            incident_id=incident_id,
        )

    def _strategy_review(self) -> None:
        metrics = self.runtime.metrics_snapshot()
        recent = self.runtime.collect_logs()
        analysis = self.ai.strategy_review(metrics=metrics, recent_events=recent)
        confidence = float(analysis.get("confidence") or 0.0)
        advice = str(analysis.get("strategy_advice") or "").strip()
        severity = str(analysis.get("severity") or "info")
        if confidence < 0.65 or (not advice and severity == "info"):
            return
        analysis["category"] = "strategy"
        analysis["needs_code_change"] = False
        evidence = "Scheduled metrics review. No automatic strategy change is permitted."
        fingerprint = self.runtime.incident_fingerprint(
            json.dumps(metrics, sort_keys=True, default=str)[:25_000],
            {"http": {"ok": not metrics.get("unavailable")}, "services": []},
        )
        incident_id = self.store.create_incident(
            fingerprint=f"strategy-{fingerprint}",
            category="strategy",
            severity=severity,
            title=str(analysis.get("title") or "Strategy review")[:300],
            summary=str(analysis.get("summary") or advice)[:4000],
            evidence=evidence,
            analysis=analysis,
            base_commit=self.runtime.current_commit(),
        )
        if incident_id is None:
            return
        message_id = self.telegram.send_incident(incident_id, analysis, evidence)
        self.store.set_message_id(incident_id, message_id)

    def run(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, self.stop)

        telegram_thread = threading.Thread(
            target=self._telegram_loop,
            daemon=True,
            name="guardian-telegram-poll",
        )
        telegram_thread.start()
        self.telegram.send_text(
            "\n".join(
                (
                    "🛡 LEGACY MODEL GUARDIAN STARTED",
                    "",
                    "Monitoring: Docker services, API readiness, worker/API/database error logs and scheduled strategy metrics.",
                    "Code changes require approval from this private chat and must pass isolated tests and review.",
                    "Telegram channel publishing is not used.",
                    "Send /status at any time.",
                )
            )
        )

        while self.running.is_set():
            started = time.monotonic()
            try:
                self._scan()
            except Exception:
                LOGGER.exception("GUARDIAN_SCAN_FAILED")
            now = time.monotonic()
            if now - self.last_strategy_review >= self.config.strategy_review_interval_seconds:
                try:
                    self._strategy_review()
                except Exception:
                    LOGGER.exception("GUARDIAN_STRATEGY_REVIEW_FAILED")
                self.last_strategy_review = now
            elapsed = time.monotonic() - started
            self.running.wait(max(1.0, self.config.scan_interval_seconds - elapsed))
