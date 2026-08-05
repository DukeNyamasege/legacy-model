"""repair REST bulk execution audit schema

Revision ID: 20260805_0020
Revises: 20260804_0019
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op


revision = "20260805_0020"
down_revision = "20260804_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # create_all() creates missing tables but never adds columns to an existing
    # VPS database. The worker writes these tables immediately before a financial
    # bulk request, so schema drift used to raise ProgrammingError and kill the
    # qualified role before the request was sent.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bulk_execution_batches (
            id VARCHAR(36) PRIMARY KEY,
            signal_id VARCHAR(36),
            run_id INTEGER,
            account_type VARCHAR(10),
            martingale_enabled BOOLEAN,
            stake DOUBLE PRECISION,
            shard_index INTEGER,
            account_count INTEGER,
            leader_managed_account_id INTEGER,
            pre_trade_profit_ratio DOUBLE PRECISION,
            request_metadata JSONB DEFAULT '{}'::jsonb,
            request_started_at TIMESTAMPTZ,
            response_received_at TIMESTAMPTZ,
            latency_ms DOUBLE PRECISION,
            status VARCHAR(30) DEFAULT 'PREPARED',
            successful_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            unique_start_time_count INTEGER,
            unique_entry_spot_count INTEGER,
            unique_expiry_time_count INTEGER,
            unique_outcome_count INTEGER,
            first_purchase_timestamp TIMESTAMPTZ,
            last_purchase_timestamp TIMESTAMPTZ,
            execution_spread_ms DOUBLE PRECISION,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )

    batch_columns = {
        "signal_id": "VARCHAR(36)",
        "run_id": "INTEGER",
        "account_type": "VARCHAR(10)",
        "martingale_enabled": "BOOLEAN DEFAULT FALSE",
        "stake": "DOUBLE PRECISION DEFAULT 0",
        "shard_index": "INTEGER DEFAULT 0",
        "account_count": "INTEGER DEFAULT 0",
        "leader_managed_account_id": "INTEGER",
        "pre_trade_profit_ratio": "DOUBLE PRECISION DEFAULT 0",
        "request_metadata": "JSONB DEFAULT '{}'::jsonb",
        "request_started_at": "TIMESTAMPTZ DEFAULT now()",
        "response_received_at": "TIMESTAMPTZ",
        "latency_ms": "DOUBLE PRECISION",
        "status": "VARCHAR(30) DEFAULT 'PREPARED'",
        "successful_count": "INTEGER DEFAULT 0",
        "failed_count": "INTEGER DEFAULT 0",
        "unique_start_time_count": "INTEGER",
        "unique_entry_spot_count": "INTEGER",
        "unique_expiry_time_count": "INTEGER",
        "unique_outcome_count": "INTEGER",
        "first_purchase_timestamp": "TIMESTAMPTZ",
        "last_purchase_timestamp": "TIMESTAMPTZ",
        "execution_spread_ms": "DOUBLE PRECISION",
        "created_at": "TIMESTAMPTZ DEFAULT now()",
    }
    for name, definition in batch_columns.items():
        op.execute(
            f"ALTER TABLE bulk_execution_batches ADD COLUMN IF NOT EXISTS {name} {definition}"
        )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bulk_execution_members (
            id BIGSERIAL PRIMARY KEY,
            batch_id VARCHAR(36),
            managed_account_id INTEGER,
            account_id_masked VARCHAR(50),
            status VARCHAR(30) DEFAULT 'PENDING',
            contract_id VARCHAR(100),
            trade_id VARCHAR(100),
            buy_price DOUBLE PRECISION,
            payout DOUBLE PRECISION,
            profit DOUBLE PRECISION,
            outcome VARCHAR(20),
            provider_start_time TIMESTAMPTZ,
            provider_expiry_time TIMESTAMPTZ,
            entry_spot DOUBLE PRECISION,
            error_code VARCHAR(80),
            error_message VARCHAR(240),
            purchase_timestamp TIMESTAMPTZ
        )
        """
    )

    member_columns = {
        "batch_id": "VARCHAR(36)",
        "managed_account_id": "INTEGER",
        "account_id_masked": "VARCHAR(50)",
        "status": "VARCHAR(30) DEFAULT 'PENDING'",
        "contract_id": "VARCHAR(100)",
        "trade_id": "VARCHAR(100)",
        "buy_price": "DOUBLE PRECISION",
        "payout": "DOUBLE PRECISION",
        "profit": "DOUBLE PRECISION",
        "outcome": "VARCHAR(20)",
        "provider_start_time": "TIMESTAMPTZ",
        "provider_expiry_time": "TIMESTAMPTZ",
        "entry_spot": "DOUBLE PRECISION",
        "error_code": "VARCHAR(80)",
        "error_message": "VARCHAR(240)",
        "purchase_timestamp": "TIMESTAMPTZ",
    }
    for name, definition in member_columns.items():
        op.execute(
            f"ALTER TABLE bulk_execution_members ADD COLUMN IF NOT EXISTS {name} {definition}"
        )

    op.execute(
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS bulk_batch_id VARCHAR(36)"
    )

    op.create_index(
        "ix_bulk_execution_batches_signal_id",
        "bulk_execution_batches",
        ["signal_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_bulk_execution_batches_status",
        "bulk_execution_batches",
        ["status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_bulk_execution_members_batch_id",
        "bulk_execution_members",
        ["batch_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_bulk_execution_members_managed_account_id",
        "bulk_execution_members",
        ["managed_account_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_bulk_execution_members_status",
        "bulk_execution_members",
        ["status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_trades_bulk_batch_id",
        "trades",
        ["bulk_batch_id"],
        unique=False,
        if_not_exists=True,
    )

    # Add constraints only when absent. NOT VALID avoids scanning historical data
    # during deployment; new rows are still checked immediately.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_bulk_batch_run_id'
            ) THEN
                ALTER TABLE bulk_execution_batches
                ADD CONSTRAINT fk_bulk_batch_run_id
                FOREIGN KEY (run_id) REFERENCES test_runs(id) NOT VALID;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_bulk_batch_leader_account'
            ) THEN
                ALTER TABLE bulk_execution_batches
                ADD CONSTRAINT fk_bulk_batch_leader_account
                FOREIGN KEY (leader_managed_account_id)
                REFERENCES managed_accounts(id) NOT VALID;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_bulk_member_batch_id'
            ) THEN
                ALTER TABLE bulk_execution_members
                ADD CONSTRAINT fk_bulk_member_batch_id
                FOREIGN KEY (batch_id) REFERENCES bulk_execution_batches(id) NOT VALID;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_bulk_member_managed_account'
            ) THEN
                ALTER TABLE bulk_execution_members
                ADD CONSTRAINT fk_bulk_member_managed_account
                FOREIGN KEY (managed_account_id) REFERENCES managed_accounts(id) NOT VALID;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_trades_bulk_batch_id'
            ) THEN
                ALTER TABLE trades
                ADD CONSTRAINT fk_trades_bulk_batch_id
                FOREIGN KEY (bulk_batch_id) REFERENCES bulk_execution_batches(id) NOT VALID;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_bulk_member_batch_account'
            ) THEN
                ALTER TABLE bulk_execution_members
                ADD CONSTRAINT uq_bulk_member_batch_account
                UNIQUE (batch_id, managed_account_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # This is a forward-only production repair. Dropping repaired columns could
    # destroy audit links for already purchased contracts, so downgrade is a no-op.
    pass
