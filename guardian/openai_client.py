from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

from .config import GuardianConfig
from .security import redact

LOGGER = logging.getLogger("legacy_model.guardian.openai")

_DIAGNOSIS_INSTRUCTIONS = """
You are the production guardian for the Father of Automation Legacy Model repository.
Diagnose only from the supplied redacted evidence and repository context.
Never request or reveal credentials, Deriv tokens, Telegram tokens, OpenAI keys,
account IDs, balances, or private user data. Never propose deleting Docker volumes,
PostgreSQL data, users, credentials, settings, or trade history. Never propose
force-pushing Git or bypassing tests. Distinguish an operational incident from a
code defect and from a strategy-performance observation. Strategy changes are
advisory only and must never be presented as guaranteed profit improvements.
Return exactly one JSON object with these keys:
category, severity, title, summary, root_cause, needs_code_change,
candidate_paths, proposed_fix, verification, strategy_advice, confidence.
category must be one of error, unhealthy, performance, strategy, security.
severity must be one of info, warning, critical.
candidate_paths must be an array of repository-relative paths.
verification must be an array of non-destructive checks.
confidence must be a number from 0 to 1.
""".strip()

_CODING_INSTRUCTIONS = """
You are creating a narrowly scoped, human-approved fix for a production Python,
FastAPI, PostgreSQL and Docker trading platform. Modify only files supplied in the
repository context or create a new non-secret source/test/documentation file when
strictly necessary. Do not modify .env files, credentials, tokens, user data,
Docker volumes, database contents, deployment state, model_artifacts, or backups.
Do not weaken authentication, OAuth, real-money gates, account isolation, Stop vs
Pause semantics, virtual-trade $0 invariants, or provider purchase safeguards.
Do not change strategy thresholds unless the approved incident explicitly asks for
a strategy change. Never claim profitability. Return exactly one JSON object:
{
  "summary": "...",
  "commit_message": "...",
  "files": [{"path": "relative/path", "content": "complete UTF-8 file"}],
  "tests": ["allow-listed non-destructive command"],
  "notes": ["..."]
}
Use complete file contents, not a diff. Keep the change minimal. Tests may use only
python/python3 compileall, unittest, pytest, docker compose config, sh -n, or
node --check. Do not include shell redirection, pipes, sudo, curl, wget, network
calls, git commands, Docker mutation commands, or database mutation commands.
""".strip()

_REVIEW_INSTRUCTIONS = """
Review a proposed production patch for the Legacy Model trading platform. Reject
it if it can expose secrets, delete or corrupt data, bypass authentication,
confuse Demo and Real accounts, purchase real contracts from virtual mode, alter
Stop/Pause semantics, send Telegram channel messages, introduce unrestricted
shell execution, force-push Git, skip tests, or make an unsupported profitability
claim. Return exactly one JSON object with keys approved, summary, risks,
required_changes. approved must be boolean; risks and required_changes arrays.
""".strip()


class GuardianOpenAI:
    def __init__(self, config: GuardianConfig) -> None:
        self.config = config
        self.client = OpenAI(api_key=config.openai_api_key, timeout=180.0)

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

    def _request(self, *, model: str, instructions: str, input_text: str) -> dict[str, Any]:
        response = self.client.responses.create(
            model=model,
            instructions=instructions,
            input=redact(input_text, maximum_chars=180_000),
        )
        return self._json_object(response.output_text)

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
        )
        payload.setdefault("category", "error")
        payload.setdefault("severity", "warning")
        payload.setdefault("title", "Guardian incident")
        payload.setdefault("summary", "An incident requires review.")
        payload.setdefault("root_cause", "Not yet confirmed")
        payload.setdefault("needs_code_change", False)
        payload.setdefault("candidate_paths", [])
        payload.setdefault("proposed_fix", "Review the evidence manually.")
        payload.setdefault("verification", [])
        payload.setdefault("strategy_advice", "")
        try:
            payload["confidence"] = min(1.0, max(0.0, float(payload.get("confidence", 0.0))))
        except (TypeError, ValueError):
            payload["confidence"] = 0.0
        return payload

    def create_patch(
        self,
        *,
        incident: dict[str, Any],
        repository_context: str,
    ) -> dict[str, Any]:
        return self._request(
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
        )

    def review_patch(
        self,
        *,
        incident: dict[str, Any],
        diff_text: str,
        test_output: str,
    ) -> dict[str, Any]:
        return self._request(
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
        )

    def strategy_review(self, *, metrics: dict[str, Any], recent_events: str) -> dict[str, Any]:
        return self._request(
            model=self.config.diagnosis_model,
            instructions=(
                _DIAGNOSIS_INSTRUCTIONS
                + "\nThis is a scheduled strategy review. Do not propose an automatic code change. "
                "Set needs_code_change=false. Focus on observed evidence, expectancy, drawdown, "
                "sample size, execution health, and what should be measured next."
            ),
            input_text=(
                "CURRENT METRICS:\n"
                + json.dumps(metrics, indent=2, default=str)
                + "\n\nRECENT REDACTED EVENTS:\n"
                + recent_events
            ),
        )
