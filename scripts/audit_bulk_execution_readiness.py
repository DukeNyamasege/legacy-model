from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import desc, func, select

from app.config import load_test2_config
from app.database import Database
from app.models import (
    BotState,
    BulkExecutionBatch,
    CandidateSignalRecord,
    ManagedAccount,
    ProposalRecord,
    Trade,
)
from app.token_store import decrypt_auth_payload
from enhanced_bot import login_identity_from_auth_payload, mask_account_id


def _purchase_api_token(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("pat_token") or "").strip()
    if explicit:
        return explicit
    auth_type = str(payload.get("auth_type") or "pat").strip().lower() or "pat"
    if auth_type == "oauth":
        return ""
    return str(payload.get("access_token") or "").strip()


def _fingerprint(secret: str) -> str:
    if not secret:
        return "-"
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:10]


def _safe_reason(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:180] or "-"


def _print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of encrypted credentials and the REST bulk-purchase "
            "pipeline. Raw credentials are never printed."
        )
    )
    parser.add_argument("--hours", type=float, default=6.0)
    args = parser.parse_args()

    config_path = os.getenv("DERIV_BOT_CONFIG", str(ROOT / "config.yaml"))
    config = load_test2_config(config_path)
    database = Database(config.database_url)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.1, args.hours))
    encryption_key = str(config.deriv.token_encryption_key or "").strip()

    _print_header("DEPLOYMENT")
    print(f"config={config_path}")
    print(f"database_configured={bool(config.database_url)}")
    print(f"token_encryption_key_configured={bool(encryption_key)}")
    print(f"audit_window_hours={max(0.1, args.hours):.2f}")

    with database.session() as session:
        accounts = list(
            session.scalars(
                select(ManagedAccount).order_by(ManagedAccount.id)
            ).all()
        )

        decoded: list[dict[str, Any]] = []
        shared_tokens: dict[str, str] = {}
        for row in accounts:
            item: dict[str, Any] = {
                "row": row,
                "decrypt_ok": False,
                "decrypt_error": "",
                "payload": {},
                "identity": "",
                "own_token": "",
            }
            try:
                payload = decrypt_auth_payload(row.token_secret, encryption_key)
                if not isinstance(payload, dict):
                    raise ValueError("decrypted credential payload is not an object")
                item["decrypt_ok"] = True
                item["payload"] = payload
                item["identity"] = login_identity_from_auth_payload(payload)
                item["own_token"] = _purchase_api_token(payload)
                if item["identity"] and item["own_token"]:
                    shared_tokens.setdefault(item["identity"], item["own_token"])
            except Exception as exc:  # audit must continue for other accounts
                item["decrypt_error"] = type(exc).__name__
            decoded.append(item)

        _print_header("MANAGED ACCOUNT CREDENTIAL INVENTORY")
        print(
            "id account enabled status auth_type account_type decrypt own_token "
            "shared_token runtime_bulk_capable fingerprint reason"
        )
        counters: Counter[str] = Counter()
        for item in decoded:
            row = item["row"]
            payload = item["payload"]
            identity = str(item["identity"] or "")
            own_token = str(item["own_token"] or "")
            shared_token = shared_tokens.get(identity, "") if identity else ""
            effective_token = own_token or shared_token
            auth_type = str(payload.get("auth_type") or "unknown").strip().lower()
            account_id = str(payload.get("account_id") or "").strip()
            account_type = str(
                payload.get("account_type")
                or payload.get("environment")
                or "unknown"
            ).strip().lower()
            runtime_auth_type = "pat" if not own_token and shared_token else auth_type
            bulk_capable = bool(effective_token) and runtime_auth_type == "pat"
            if bool(row.enabled):
                counters["enabled"] += 1
            if item["decrypt_ok"]:
                counters["decrypt_ok"] += 1
            else:
                counters["decrypt_failed"] += 1
            if own_token:
                counters["own_token"] += 1
            if shared_token:
                counters["shared_token"] += 1
            if bulk_capable:
                counters["bulk_capable"] += 1
            if own_token and auth_type == "oauth":
                counters["pat_auth_type_mismatch"] += 1
            if str(row.execution_status or "").lower() in {
                "credential_error",
                "token_required",
                "bulk_execution_pat_required",
            }:
                counters["token_status_blocked"] += 1

            print(
                f"{row.id} "
                f"{mask_account_id(account_id) if account_id else 'missing'} "
                f"{str(bool(row.enabled)).lower()} "
                f"{row.execution_status or '-'} "
                f"{auth_type or '-'} "
                f"{account_type or '-'} "
                f"{str(bool(item['decrypt_ok'])).lower()} "
                f"{str(bool(own_token)).lower()} "
                f"{str(bool(shared_token)).lower()} "
                f"{str(bool(bulk_capable)).lower()} "
                f"{_fingerprint(effective_token)} "
                f"{_safe_reason(row.execution_status_reason or item['decrypt_error'])}"
            )

        print(
            "SUMMARY "
            f"total={len(accounts)} enabled={counters['enabled']} "
            f"decrypt_ok={counters['decrypt_ok']} "
            f"decrypt_failed={counters['decrypt_failed']} "
            f"own_token={counters['own_token']} shared_token={counters['shared_token']} "
            f"bulk_capable={counters['bulk_capable']} "
            f"token_status_blocked={counters['token_status_blocked']} "
            f"pat_auth_type_mismatch={counters['pat_auth_type_mismatch']}"
        )

        _print_header("BOT STATE")
        bot_states = list(session.scalars(select(BotState).order_by(BotState.run_id)).all())
        if not bot_states:
            print("none")
        for state in bot_states:
            print(
                f"run_id={state.run_id} status={state.status} "
                f"pause_reason={_safe_reason(state.pause_reason)} "
                f"heartbeat={state.last_heartbeat.isoformat() if state.last_heartbeat else '-'} "
                f"cooldown_ticks={state.cooldown_ticks_remaining}"
            )

        status_rows = session.execute(
            select(
                CandidateSignalRecord.final_status,
                func.count(CandidateSignalRecord.signal_id),
            )
            .where(CandidateSignalRecord.generated_timestamp >= cutoff)
            .group_by(CandidateSignalRecord.final_status)
            .order_by(desc(func.count(CandidateSignalRecord.signal_id)))
        ).all()
        candidate_count = sum(int(count or 0) for _status, count in status_rows)
        proposal_count = int(
            session.scalar(
                select(func.count(ProposalRecord.id)).where(
                    ProposalRecord.response_timestamp >= cutoff
                )
            )
            or 0
        )
        batch_count = int(
            session.scalar(
                select(func.count(BulkExecutionBatch.id)).where(
                    BulkExecutionBatch.request_started_at >= cutoff
                )
            )
            or 0
        )
        trade_count = int(
            session.scalar(
                select(func.count(Trade.id)).where(Trade.purchase_time >= cutoff)
            )
            or 0
        )

        _print_header("RECENT EXECUTION FUNNEL")
        print(
            f"since={cutoff.isoformat()} candidates={candidate_count} "
            f"proposals={proposal_count} bulk_batches={batch_count} trades={trade_count}"
        )
        if status_rows:
            print("candidate_status_counts:")
            for status, count in status_rows:
                print(f"  {status or 'UNKNOWN'}={int(count or 0)}")
        else:
            print("candidate_status_counts: none")

        recent_signals = session.execute(
            select(
                CandidateSignalRecord.generated_timestamp,
                CandidateSignalRecord.symbol,
                CandidateSignalRecord.contract_type,
                CandidateSignalRecord.barrier,
                CandidateSignalRecord.final_status,
                CandidateSignalRecord.consumed,
                CandidateSignalRecord.stale,
            )
            .order_by(CandidateSignalRecord.generated_timestamp.desc())
            .limit(12)
        ).all()
        print("latest_signals:")
        if not recent_signals:
            print("  none")
        for row in recent_signals:
            print(
                "  "
                f"time={row.generated_timestamp.isoformat()} symbol={row.symbol} "
                f"contract={row.contract_type} barrier={row.barrier} "
                f"status={row.final_status} consumed={row.consumed} stale={row.stale}"
            )

        recent_batches = session.execute(
            select(
                BulkExecutionBatch.request_started_at,
                BulkExecutionBatch.id,
                BulkExecutionBatch.status,
                BulkExecutionBatch.successful_count,
                BulkExecutionBatch.failed_count,
                BulkExecutionBatch.request_metadata,
            )
            .order_by(BulkExecutionBatch.request_started_at.desc())
            .limit(8)
        ).all()
        print("latest_bulk_batches:")
        if not recent_batches:
            print("  none")
        for row in recent_batches:
            metadata = row.request_metadata or {}
            print(
                "  "
                f"time={row.request_started_at.isoformat()} batch={row.id} "
                f"status={row.status} success={row.successful_count} failed={row.failed_count} "
                f"strategy={metadata.get('strategy_group', '-')} "
                f"contract={metadata.get('contract_type', '-')} "
                f"barrier={metadata.get('barrier', '-')}"
            )

    _print_header("PRIMARY DIAGNOSIS")
    enabled_bulk_capable = sum(
        1
        for item in decoded
        if bool(item["row"].enabled)
        and bool(item["decrypt_ok"])
        and bool(
            item["own_token"]
            or shared_tokens.get(str(item["identity"] or ""), "")
        )
        and (
            (
                "pat"
                if not item["own_token"]
                and shared_tokens.get(str(item["identity"] or ""), "")
                else str(item["payload"].get("auth_type") or "unknown").lower()
            )
            == "pat"
        )
    )
    if not encryption_key:
        print("BLOCKER=DERIV_TOKEN_ENCRYPTION_KEY is missing in the worker environment")
    elif not accounts:
        print("BLOCKER=no managed_accounts rows exist")
    elif counters["decrypt_failed"]:
        print("BLOCKER=one or more stored credentials cannot be decrypted with the worker key")
    elif counters["pat_auth_type_mismatch"]:
        print("BLOCKER=API token exists but auth_type is oauth; runtime bulk capability rejects it")
    elif enabled_bulk_capable == 0:
        print("BLOCKER=no enabled account has an effective REST bulk API token")
    elif candidate_count == 0:
        print(
            "BLOCKER=no candidate signal was created in the audit window; inspect "
            "AIDR_DIGIT_PREFILTER, candidate freshness and strategy thresholds"
        )
    elif proposal_count == 0:
        print("BLOCKER=candidates exist but no provider proposal was accepted")
    elif batch_count == 0:
        print(
            "BLOCKER=proposals exist but no REST bulk batch was created; inspect account "
            "scope, token status, markup configuration and dispatch logs"
        )
    elif trade_count == 0:
        print("BLOCKER=bulk batches exist but no local trades were registered")
    else:
        print("READY=the recent execution funnel contains registered purchases")

    print("raw_tokens_printed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
