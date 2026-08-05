from __future__ import annotations

from pathlib import Path

from app.seamless_execution_runtime import _normalize_bulk_response


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "app" / "seamless_execution_runtime.py"
FINAL_RUNTIME = ROOT / "app" / "seamless_execution_final.py"
PERSONAL = ROOT / "app" / "seamless_personal_execution.py"
FINAL_PERSONAL = ROOT / "app" / "seamless_personal_execution_final.py"
WORKER_INSTALLER = ROOT / "app" / "production_worker_integration.py"
API_INSTALLER = ROOT / "app" / "database_runtime_hardening.py"
MIGRATION = (
    ROOT
    / "alembic"
    / "versions"
    / "20260805_0020_bulk_execution_schema_repair.py"
)


def test_bulk_response_normalizes_nested_transactions_and_errors() -> None:
    response = {
        "data": {
            "transactions": [
                {
                    "transaction": {
                        "account_id": "DOT111",
                        "contract_id": "9001",
                        "transaction_id": "7001",
                    }
                }
            ]
        },
        "errors": [
            {
                "account_id": "DOT222",
                "code": "InvalidToken",
                "message": "trade scope missing",
            }
        ],
    }
    normalized = _normalize_bulk_response(
        response,
        {"accounts": [{"account_id": "DOT111"}, {"account_id": "DOT222"}]},
    )

    assert normalized["data"]["transactions"] == [
        {
            "account_id": "DOT111",
            "contract_id": "9001",
            "transaction_id": "7001",
        }
    ]
    assert normalized["errors"] == [
        {
            "account_id": "DOT222",
            "code": "InvalidToken",
            "message": "trade scope missing",
        }
    ]


def test_bulk_response_maps_ordered_results_without_account_ids() -> None:
    normalized = _normalize_bulk_response(
        {
            "results": [
                {"contractId": "1001", "transactionId": "2001"},
                {"contractId": "1002", "transactionId": "2002"},
            ]
        },
        {"accounts": [{"account_id": "DOT101"}, {"account_id": "DOT202"}]},
    )

    transactions = normalized["data"]["transactions"]
    assert [item["account_id"] for item in transactions] == ["DOT101", "DOT202"]
    assert [item["contract_id"] for item in transactions] == ["1001", "1002"]
    assert [item["transaction_id"] for item in transactions] == ["2001", "2002"]


def test_bulk_response_supports_account_keyed_mapping() -> None:
    normalized = _normalize_bulk_response(
        {
            "data": {
                "DOT301": {"contract_id": "3001"},
                "DOT302": {"contract_id": "3002"},
            }
        },
        {"accounts": [{"account_id": "DOT301"}, {"account_id": "DOT302"}]},
    )
    transactions = normalized["data"]["transactions"]
    assert {(item["account_id"], item["contract_id"]) for item in transactions} == {
        ("DOT301", "3001"),
        ("DOT302", "3002"),
    }


def test_programming_error_is_audit_only_and_exactly_logged() -> None:
    source = RUNTIME.read_text(encoding="utf-8")

    assert "except ProgrammingError as exc:" in source
    assert "BULK_AUDIT_SCHEMA_DEGRADED" in source
    assert "financial_execution_continues=true" in source
    assert "duplicate_retry=false" in source
    assert "sqlstate=%s constraint=%s" in source
    assert "AIDR_ROLE_DISPATCH_FAILED" in source
    assert "error_type=%s sqlstate=%s error=%s" in source


def test_stop_rejoin_and_history_semantics_are_seamless() -> None:
    source = FINAL_PERSONAL.read_text(encoding="utf-8")
    base_source = PERSONAL.read_text(encoding="utf-8")

    assert 'row.execution_status = "settlement_only" if settlement_only else "stopped"' in source
    assert "_reset_risk_state(session, managed_id)" in source
    assert "next_start_uses_base_stake" in source
    assert "stored_pat_reused" in source
    assert "shared_pat_reused" in source
    assert "pause_preserves_recovery" in source
    assert "session.execute(delete(Trade" not in source
    assert "session.execute(delete(VirtualTrade" not in source
    assert "session.execute(delete(Trade" not in base_source
    assert "session.execute(delete(VirtualTrade" not in base_source
    assert "database_records_preserved" in base_source


def test_strategy_switch_queues_while_contract_is_open() -> None:
    source = FINAL_PERSONAL.read_text(encoding="utf-8")
    runtime = FINAL_RUNTIME.read_text(encoding="utf-8")

    assert "queued = bool(changed and open_count)" in source
    assert "_write_pending(session, managed_id, requested)" in source
    assert "queued_until_settlement" in source
    assert "PENDING_STRATEGY_ACTIVATED" in runtime
    assert "open_contracts=0 recovery_reset=true" in runtime
    assert "_apply_pending_strategies(bot)" in runtime


def test_market_and_settlement_only_accounts_are_financially_filtered() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    final_source = FINAL_RUNTIME.read_text(encoding="utf-8")

    assert "personal_execution_market:" in source
    assert "_install_market_scope_filter" in source
    assert "settlement_only" in final_source
    assert "_install_financial_scope_filter" in final_source
    assert "not _token_is_settlement_only" in final_source
    assert "strict_guard.STOPPED_STATUSES.add" in final_source
    assert "SETTLEMENT_ONLY_FINALIZED" in final_source


def test_placeholder_contracts_are_removed_from_reconciliation_noise() -> None:
    source = FINAL_RUNTIME.read_text(encoding="utf-8")

    assert "0 < contract_id < 1_000_000" in source
    assert "LEGACY_PLACEHOLDER_CONTRACT_IGNORED" in source
    assert "financial_impact=0" in source


def test_dashboard_appended_script_does_not_reuse_old_content_length() -> None:
    source = FINAL_PERSONAL.read_text(encoding="utf-8")

    assert 'str(key).lower() == "content-length"' in source
    assert '"X-FOA-Seamless-Execution": "2"' in source


def test_bulk_schema_repair_runs_before_services() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260805_0020"' in source
    assert 'down_revision = "20260804_0019"' in source
    assert "CREATE TABLE IF NOT EXISTS bulk_execution_batches" in source
    assert "ADD COLUMN IF NOT EXISTS request_metadata" in source
    assert "CREATE TABLE IF NOT EXISTS bulk_execution_members" in source
    assert "ADD COLUMN IF NOT EXISTS bulk_batch_id" in source


def test_final_installers_are_last_authorities() -> None:
    worker = WORKER_INSTALLER.read_text(encoding="utf-8")
    api = API_INSTALLER.read_text(encoding="utf-8")

    assert worker.index("install_seamless_execution_runtime()") < worker.index(
        "install_final_seamless_execution_runtime()"
    )
    assert api.index("install_seamless_personal_execution(app)") < api.index(
        "install_final_seamless_personal_execution(app)"
    )
