from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GuardianStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._initialize()
        self.interrupted_remediations = self.close_interrupted_remediations()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    base_commit TEXT NOT NULL DEFAULT '',
                    proposal_message_id INTEGER,
                    approved_by TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                DROP INDEX IF EXISTS uq_incident_active_fingerprint;
                CREATE UNIQUE INDEX IF NOT EXISTS uq_incident_active_fingerprint_v2
                ON incidents(fingerprint)
                WHERE status IN ('proposed', 'approved', 'working');

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def create_incident(
        self,
        *,
        fingerprint: str,
        category: str,
        severity: str,
        title: str,
        summary: str,
        evidence: str,
        analysis: dict[str, Any],
        base_commit: str,
    ) -> int | None:
        now = _now()
        try:
            with self.connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO incidents(
                        fingerprint, category, severity, status, title, summary,
                        evidence, analysis_json, base_commit, created_at, updated_at
                    ) VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        category,
                        severity,
                        title,
                        summary,
                        evidence,
                        json.dumps(analysis, separators=(",", ":"), default=str),
                        base_commit,
                        now,
                        now,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def incident(self, incident_id: int) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (int(incident_id),)
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        for key in ("analysis_json", "result_json"):
            try:
                payload[key.removesuffix("_json")] = json.loads(payload.get(key) or "{}")
            except ValueError:
                payload[key.removesuffix("_json")] = {}
        return payload

    def set_message_id(self, incident_id: int, message_id: int) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE incidents SET proposal_message_id = ?, updated_at = ? WHERE id = ?",
                (int(message_id), _now(), int(incident_id)),
            )

    def transition(
        self,
        incident_id: int,
        *,
        expected: tuple[str, ...],
        target: str,
        approved_by: str = "",
        result: dict[str, Any] | None = None,
    ) -> bool:
        if not expected:
            raise ValueError("At least one expected incident state is required")
        placeholders = ",".join("?" for _ in expected)
        with self.connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE incidents
                SET status = ?, approved_by = CASE WHEN ? <> '' THEN ? ELSE approved_by END,
                    result_json = ?, updated_at = ?
                WHERE id = ? AND status IN ({placeholders})
                """,
                [
                    target,
                    approved_by,
                    approved_by,
                    json.dumps(result or {}, separators=(",", ":"), default=str),
                    _now(),
                    int(incident_id),
                    *expected,
                ],
            )
            return cursor.rowcount == 1

    def close_interrupted_remediations(self) -> list[dict[str, Any]]:
        """Fail closed after a service/VPS restart during an approved patch."""

        now = _now()
        result = {
            "error": (
                "Guardian restarted before this approved remediation completed. "
                "It will not resume automatically; a new diagnosis and approval are required."
            )
        }
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, proposal_message_id, status, title
                FROM incidents
                WHERE status IN ('approved', 'working')
                ORDER BY id
                """
            ).fetchall()
            if rows:
                connection.execute(
                    """
                    UPDATE incidents
                    SET status = 'interrupted', result_json = ?, updated_at = ?
                    WHERE status IN ('approved', 'working')
                    """,
                    (json.dumps(result, separators=(",", ":")), now),
                )
        return [dict(row) for row in rows]

    def add_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        incident_id: int | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO events(incident_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    int(incident_id) if incident_id is not None else None,
                    str(event_type),
                    json.dumps(payload, separators=(",", ":"), default=str),
                    _now(),
                ),
            )

    def get(self, key: str, default: str = "") -> str:
        with self.connection() as connection:
            row = connection.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else default

    def set(self, key: str, value: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO kv(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(key), str(value), _now()),
            )
