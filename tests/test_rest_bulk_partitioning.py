from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rest_bulk_partitioning_groups_by_strategy_contract_mode_and_stake() -> None:
    source = (ROOT / "app" / "rest_bulk_partitioning.py").read_text(encoding="utf-8")
    assert "class BulkPartitionKey" in source
    for marker in (
        "account_type",
        "family",
        "side",
        "role",
        "symbol",
        "contract_type",
        "barrier",
        "stake",
    ):
        assert marker in source
    assert "REST_BULK_PARTITION_READY" in source
    assert "same_contract_per_request=true" in source
    assert "MAX_BULK_ACCOUNTS_PER_REQUEST = 100" in source


def test_rest_bulk_partitioning_enforces_three_percent_markup_and_api_token_notice() -> None:
    source = (ROOT / "app" / "rest_bulk_partitioning.py").read_text(encoding="utf-8")
    assert "REQUIRED_APP_MARKUP_PERCENTAGE = 3.0" in source
    assert "APP_MARKUP_NOT_CONFIGURED" in source
    assert "markup_source=registered_deriv_app_id" in source
    assert "Please link your Deriv API token with trade scope" in source
    assert "Security & limits" in source
    assert "API_TOKEN_REQUIRED" in source


def test_worker_reapplies_rest_partitioning_after_scalable_layers() -> None:
    source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    first_scalable = source.index("install_scalable_group_execution()")
    first_partition_after_scalable = source.index(
        "install_rest_bulk_partitioning()",
        first_scalable,
    )
    hardening = source.index("install_scalable_group_execution_hardening()")
    second_partition = source.index("install_rest_bulk_partitioning()", hardening)
    production = source.index("install_production_worker_integration()")
    assert first_scalable < first_partition_after_scalable < hardening
    assert hardening < second_partition < production


def test_dashboard_token_notice_uses_user_language() -> None:
    source = (ROOT / "dashboard" / "account-lifecycle.js").read_text(encoding="utf-8")
    assert "Deriv API token" in source
    assert "Security & limits" in source
    assert "trade permission" in source
    assert "Personal Access Token" not in source
