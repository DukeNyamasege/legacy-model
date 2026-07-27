\set ON_ERROR_STOP on

-- CLEAN TRADING LEDGER RESET
--
-- Run only while API and worker are stopped and only after a pg_dump backup.
-- This intentionally removes historical trading/model/tick/recovery data so the
-- next strategy starts at zero. It deliberately preserves managed_accounts,
-- their encrypted credentials/settings, client_sessions, oauth_login_states,
-- Telegram settings/cache, and general runtime configuration such as trading_mode.

BEGIN;

-- test_runs is the parent of all run-scoped trading/model tables. PostgreSQL
-- CASCADE clears ticks, candidate/model decision/proposal/trade records,
-- directional/shadow/virtual/system-model ledgers, bulk execution rows,
-- account snapshots and BotState rows without touching managed_accounts.
TRUNCATE TABLE test_runs RESTART IDENTITY CASCADE;

-- These operational trading states are account/global scoped rather than
-- TestRun-scoped and therefore need an explicit clean reset.
TRUNCATE TABLE account_risk_states RESTART IDENTITY;
TRUNCATE TABLE dashboard_snapshots RESTART IDENTITY;
TRUNCATE TABLE trader_leases RESTART IDENTITY;

-- Remove only caches/state tied to every known hybrid trading epoch. Keep
-- trading_mode and unrelated preferences intact. V3 uses the v2 state namespace;
-- clearing both namespaces guarantees a clean PRIMARY_DIGITS restart.
DELETE FROM runtime_preferences
WHERE preference_key = 'hybrid_o2u7_put_v1:state'
   OR preference_key LIKE 'hybrid_o2u7_put_v1:account_epoch:%'
   OR preference_key = 'hybrid_o2u7_put_v2:state'
   OR preference_key LIKE 'hybrid_o2u7_put_v2:account_epoch:%'
   OR preference_key LIKE 'dashboard_reference_managed_account:%';

-- Do not change enabled/disabled choices or account stakes. Enabled accounts are
-- revalidated by the fresh worker and will start from base stake because all
-- AccountRiskState recovery debt has been removed.

INSERT INTO audit_events(action, actor, source_ip, details, created_at)
VALUES (
    'TRADING_DATA_RESET',
    'VPS_ADMIN',
    'localhost',
    '{"scope":"all_trading_data","managed_accounts_preserved":true,"credentials_preserved":true,"account_settings_preserved":true,"all_hybrid_recovery_epochs_cleared":true}'::json,
    NOW()
);

COMMIT;

-- Every count below must be zero before the new strategy is started.
SELECT 'test_runs' AS dataset, COUNT(*) AS rows FROM test_runs
UNION ALL SELECT 'ticks', COUNT(*) FROM ticks
UNION ALL SELECT 'candidate_signals', COUNT(*) FROM candidate_signals
UNION ALL SELECT 'model_decisions', COUNT(*) FROM model_decisions
UNION ALL SELECT 'proposals', COUNT(*) FROM proposals
UNION ALL SELECT 'trades', COUNT(*) FROM trades
UNION ALL SELECT 'streaks', COUNT(*) FROM streaks
UNION ALL SELECT 'bot_state', COUNT(*) FROM bot_state
UNION ALL SELECT 'account_snapshots', COUNT(*) FROM account_snapshots
UNION ALL SELECT 'directional_signals', COUNT(*) FROM directional_signals
UNION ALL SELECT 'shadow_contracts', COUNT(*) FROM shadow_contracts
UNION ALL SELECT 'virtual_trades', COUNT(*) FROM virtual_trades
UNION ALL SELECT 'virtual_guard_state', COUNT(*) FROM virtual_guard_state
UNION ALL SELECT 'system_model_trades', COUNT(*) FROM system_model_trades
UNION ALL SELECT 'system_model_states', COUNT(*) FROM system_model_states
UNION ALL SELECT 'bulk_execution_batches', COUNT(*) FROM bulk_execution_batches
UNION ALL SELECT 'bulk_execution_members', COUNT(*) FROM bulk_execution_members
UNION ALL SELECT 'account_risk_states', COUNT(*) FROM account_risk_states
UNION ALL SELECT 'dashboard_snapshots', COUNT(*) FROM dashboard_snapshots
UNION ALL SELECT 'trader_leases', COUNT(*) FROM trader_leases
ORDER BY dataset;
