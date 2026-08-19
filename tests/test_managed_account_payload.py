from __future__ import annotations

from datetime import datetime, timezone

from app.models import ManagedAccount
from app.repositories.test2_repository import Test2Repository as Repository


def managed_account() -> ManagedAccount:
    timestamp = datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc)
    row = ManagedAccount(
        label="Primary",
        token_secret="encrypted-token",
        enabled=True,
        stake_amount=0.5,
        take_profit=12.0,
        stop_loss=6.0,
        martingale_enabled=True,
        execution_status="active",
        execution_status_reason="Running",
        execution_status_updated_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )
    row.id = 42
    return row


def test_public_managed_account_payload_is_serialized_and_secret_free() -> None:
    payload = Repository._managed_account_payload(managed_account())

    assert payload["id"] == 42
    assert payload["created_at"] == "2026-08-19T12:30:00+00:00"
    assert "token_secret" not in payload
    assert "execution_status_updated_at" not in payload


def test_private_managed_account_payload_preserves_internal_fields() -> None:
    row = managed_account()
    payload = Repository._managed_account_payload(
        row,
        include_secret=True,
        serialize_timestamps=False,
    )

    assert payload["token_secret"] == "encrypted-token"
    assert payload["created_at"] is row.created_at
    assert payload["execution_status_updated_at"] is row.execution_status_updated_at
