from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any

from openai import BadRequestError, OpenAI

from .config import GuardianConfig
from .privacy import sanitize_strategy_metrics
from .security import redact

LOGGER = logging.getLogger("legacy_model.guardian.openai")

_DIAGNOSIS_INSTRUCTIONS = """
You are the production guardian for the Father of Automation Legacy Model repository.
Diagnose only from the supplied project charter, redacted evidence and repository
context. Never request or reveal credentials, Deriv tokens, Telegram tokens,
OpenAI keys, account IDs, balances, or private user data. Never propose deleting
Docker volumes, PostgreSQL data, users, credentials, settings, or trade history.
Never propose force-pushing Git or bypassing tests. Distinguish an operational
incident from a code defect and from a strategy-performance observation. Strategy
changes are advisory only and must never be presented as guaranteed profit
improvements.
""".strip()

_CODING_INSTRUCTIONS = """
You are creating a narrowly scoped, human-approved fix for a production Python,
FastAPI, PostgreSQL and Docker trading platform. Follow the supplied project
charter exactly. Modify only files supplied in the repository context or create a
new non-secret source/test/documentation file when strictly necessary. Do not
modify .env files, credentials, tokens, user data, Docker volumes, database
contents, deployment state, model_artifacts, or backups. Do not weaken
authentication, OAuth, real-money gates, account isolation, Stop vs Pause
semantics, virtual-trade $0 invariants, or provider purchase safeguards. Do not
change strategy thresholds, stakes, TP/SL or recovery rules in this incident
pipeline. Never claim profitability. Use complete UTF-8 file contents, not a diff,
and keep the change minimal. Tests may use only Python compileall, unittest,
pytest, or `sh -n`. Do not include shell redirection, pipes, sudo, curl, wget,
network calls, git commands, Docker commands, or database mutation commands.
""".strip()

_REVIEW_INSTRUCTIONS = """
Review a proposed production patch for the Legacy Model trading platform against
the supplied project charter. Reject it if it can expose secrets, delete or
corrupt data, bypass authentication, confuse Demo and Real accounts, purchase
real contracts from virtual mode, alter Stop/Pause semantics, send Telegram
channel messages, introduce unrestricted shell execution, force-push Git, skip
tests, change strategy/stake/recovery rules through an incident fix, or make an
unsupported profitability claim.
""".strip()

_DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "category": {
            "type": "string",
            "enum": ["error", "unhealthy", "performance", "strategy", "security"],
        },
        "severity": {
            "type": "string",
            "enum": ["info", "warning", "critical"],
        },
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "root_cause": {"type": "string"},
        "needs_code_change": {"type": "boolean"},
        "candidate_paths": {
            "type": "array",
            "items": {"type": "string"},
        },
        "proposed_fix": {"type": "string"},
        "verification": {
            "type": "array",
            "items": {"type": "string"},
        },
        "strategy_advice": {"type": "string"},
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
    },
    "required": [
        "category",
        "severity",
        "title",
        "summary",
        "root_cause",
        "needs_code_change",
        "candidate_paths",
        "proposed_fix",
        "verification",
        "strategy_advice",
        "confidence",
    ],
}

_PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "commit_message": {"type": "string"},
        "files": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        "tests": {
            "type": "array",
            "items": {"type": "string"},
        },
        "notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "commit_message", "files", "tests", "notes"],
}

_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "approved": {"type": "boolean"},
        "summary": {"type": "string"},
        "risks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "required_changes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["approved", "summary", "risks", "required_changes"],
}


class GuardianOpenAI:
    def __init__(self, config: GuardianConfig) -> None:
        self.config = config
        charter_path = config.repo_dir / "guardian" / "PROJECT_CHARTER.md"
        if not charter_path.is_file():
            raise RuntimeError(f"Guardian project charter is missing: {charter_path}")
        self.project_charter = redact(
            charter_path.read_text(encoding="utf-8", errors="replace"),
            maximum_chars=45_000,
        )
        strategy_contract_path = config.repo_dir / "app" / "aidr_strategy_contract.json"
        if not strategy_contract_path.is_file():
            raise RuntimeError(
                f"Guardian strategy contract is missing: {strategy_contract_path}"
            )
        self.strategy_contract = redact(
            strategy_contract_path.read_text(encoding="utf-8", errors="strict"),
            maximum_chars=20_000,
        )
        self._budget_lock = threading.Lock()
        self.client = OpenAI(api_key=config.openai_api_key, timeout=180.0)

    def _consume_ai_call(self, *, model: str, purpose: str) -> None:
        """Reserve one daily API call before sending any model request."""

        day = datetime.now(timezone.utc).date().isoformat()
        path = self.config.ai_budget_path
        with self._budget_lock:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    payload = {}
            except (OSError, ValueError, TypeError):
                payload = {}
            try:
                count = (
                    int(payload.get("count") or 0)
                    if payload.get("day") == day
                    else 0
                )
            except (TypeError, ValueError):
                count = 0
            if count >= self.config.maximum_ai_calls_per_day:
                raise RuntimeError(
                    "Guardian daily OpenAI call budget is exhausted "
                    f"({count}/{self.config.maximum_ai_calls_per_day}). "
                    "Monitoring continues, but new AI diagnoses and patches wait for the next UTC day."
                )
            next_payload = {
                "day": day,
                "count": count + 1,
                "limit": self.config.maximum_ai_calls_per_day,
                "last_model": str(model)[:120],
                "last_purpose": str(purpose)[:120],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(next_payload, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(path)
            path.chmod(0o600)

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("OpenAI response did not contain a JSON object")
            value = json.loads(raw[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("OpenAI response JSON was not an object")
        return value

    def _request(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        trusted_instructions = (
            instructions
            + "\n\nTRUSTED PROJECT CHARTER:\n"
            + self.project_charter
            + "\n\nAUTHORITATIVE MACHINE-READABLE STRATEGY CONTRACT:\n"
            + self.strategy_contract
            + "\n\nReturn only the requested structured object."
        )
        safe_input = redact(input_text, maximum_chars=180_000)
        self._consume_ai_call(model=model, purpose=schema_name)
        try:
            response = self.client.responses.create(
                model=model,
                instructions=trusted_instructions,
                input=safe_input,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
                store=False,
            )
        except BadRequestError as exc:
            # Only a schema/parameter rejection receives a second plain-JSON
            # attempt. Authentication, rate-limit, timeout and network failures
            # fail immediately instead of doubling cost or hiding the cause.
            LOGGER.warning(
                "GUARDIAN_STRUCTURED_OUTPUT_FALLBACK model=%s error=%s",
                model,
                type(exc).__name__,
            )
            self._consume_ai_call(model=model, purpose=f"{schema_name}_fallback")
            response = self.client.responses.create(
                model=model,
                instructions=(
                    trusted_instructions
                    + "\nReturn one valid JSON object matching this JSON Schema exactly:\n"
                    + json.dumps(schema, separators=(",", ":"))
                ),
                input=safe_input,
                store=False,
            )
        return self._json_object(response.output_text)

    @staticmethod
    def _normalise_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
        categories = {"error", "unhealthy", "performance", "strategy", "security"}
        severities = {"info", "warning", "critical"}
        category = str(payload.get("category") or "error").lower()
        severity = str(payload.get("severity") or "warning").lower()
        payload["category"] = category if category in categories else "error"
        payload["severity"] = severity if severity in severities else "warning"
        payload["title"] = str(payload.get("title") or "Guardian incident")[:300]
        payload["summary"] = str(
            payload.get("summary") or "An incident requires review."
        )[:5000]
        payload["root_cause"] = str(
            payload.get("root_cause") or "Not yet confirmed"
        )[:5000]
        payload["proposed_fix"] = str(
            payload.get("proposed_fix") or "Review the evidence manually."
        )[:5000]
        payload["strategy_advice"] = str(
            payload.get("strategy_advice") or ""
        )[:5000]
        paths = payload.get("candidate_paths")
        payload["candidate_paths"] = [
            str(path).strip().replace("\\", "/")
            for path in (paths if isinstance(paths, list) else [])
            if str(path).strip()
        ][:10]
        checks = payload.get("verification")
        payload["verification"] = [
            str(item).strip()
            for item in (checks if isinstance(checks, list) else [])
            if str(item).strip()
        ][:10]
        payload["needs_code_change"] = bool(payload.get("needs_code_change"))
        if payload["category"] in {"strategy", "performance"}:
            payload["needs_code_change"] = False
        try:
            payload["confidence"] = min(
                1.0,
                max(0.0, float(payload.get("confidence", 0.0))),
            )
        except (TypeError, ValueError):
            payload["confidence"] = 0.0
        return payload

    def diagnose(
        self,
        *,
        evidence: str,
        health: dict[str, Any],
        repository_context: str,
    ) -> dict[str, Any]:
        payload = self._request(
            model=self.config.diagnosis_model,
            instructions=_DIAGNOSIS_INSTRUCTIONS,
            input_text=(
                "HEALTH SNAPSHOT:\n"
                + json.dumps(health, indent=2, default=str)
                + "\n\nREDACTED LOG EVIDENCE:\n"
                + evidence
                + "\n\nREPOSITORY CONTEXT:\n"
                + repository_context
            ),
            schema_name="guardian_diagnosis",
            schema=_DIAGNOSIS_SCHEMA,
        )
        return self._normalise_diagnosis(payload)

    def create_patch(
        self,
        *,
        incident: dict[str, Any],
        repository_context: str,
    ) -> dict[str, Any]:
        payload = self._request(
            model=self.config.coding_model,
            instructions=_CODING_INSTRUCTIONS,
            input_text=(
                "APPROVED INCIDENT:\n"
                + json.dumps(
                    {
                        "id": incident.get("id"),
                        "title": incident.get("title"),
                        "summary": incident.get("summary"),
                        "analysis": incident.get("analysis", {}),
                        "base_commit": incident.get("base_commit", ""),
                    },
                    indent=2,
                    default=str,
                )
                + "\n\nREPOSITORY FILES AVAILABLE FOR EDITING:\n"
                + repository_context
            ),
            schema_name="guardian_patch",
            schema=_PATCH_SCHEMA,
        )
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("Coding model returned no replacement files")
        payload["summary"] = str(payload.get("summary") or "Approved fix")[:5000]
        payload["commit_message"] = str(
            payload.get("commit_message") or "Guardian approved production fix"
        )[:120]
        payload["tests"] = [
            str(item).strip()
            for item in (payload.get("tests") or [])
            if str(item).strip()
        ][:8]
        payload["notes"] = [
            str(item).strip()
            for item in (payload.get("notes") or [])
            if str(item).strip()
        ][:20]
        return payload

    def review_patch(
        self,
        *,
        incident: dict[str, Any],
        diff_text: str,
        test_output: str,
    ) -> dict[str, Any]:
        payload = self._request(
            model=self.config.reviewer_model,
            instructions=_REVIEW_INSTRUCTIONS,
            input_text=(
                "INCIDENT:\n"
                + json.dumps(
                    {
                        "title": incident.get("title"),
                        "summary": incident.get("summary"),
                        "analysis": incident.get("analysis", {}),
                    },
                    indent=2,
                    default=str,
                )
                + "\n\nPATCH DIFF:\n"
                + diff_text
                + "\n\nTEST OUTPUT:\n"
                + test_output
            ),
            schema_name="guardian_patch_review",
            schema=_REVIEW_SCHEMA,
        )
        return {
            "approved": bool(payload.get("approved")),
            "summary": str(payload.get("summary") or "Review incomplete")[:5000],
            "risks": [
                str(item).strip()
                for item in (payload.get("risks") or [])
                if str(item).strip()
            ][:20],
            "required_changes": [
                str(item).strip()
                for item in (payload.get("required_changes") or [])
                if str(item).strip()
            ][:20],
        }

    def strategy_review(
        self,
        *,
        metrics: dict[str, Any],
        recent_events: str,
    ) -> dict[str, Any]:
        safe_metrics = sanitize_strategy_metrics(metrics)
        payload = self._request(
            model=self.config.diagnosis_model,
            instructions=(
                _DIAGNOSIS_INSTRUCTIONS
                + "\nThis is a scheduled strategy review. Do not propose an automatic "
                "code change. Set needs_code_change=false. Focus on observed "
                "expectancy, payout/break-even economics, drawdown, sample size, "
                "execution health and what should be measured next."
            ),
            input_text=(
                "AGGREGATE SANITIZED METRICS:\n"
                + json.dumps(safe_metrics, indent=2, default=str)
                + "\n\nRECENT REDACTED EVENTS:\n"
                + recent_events
            ),
            schema_name="guardian_strategy_review",
            schema=_DIAGNOSIS_SCHEMA,
        )
        payload = self._normalise_diagnosis(payload)
        payload["category"] = "strategy"
        payload["needs_code_change"] = False
        return payload
