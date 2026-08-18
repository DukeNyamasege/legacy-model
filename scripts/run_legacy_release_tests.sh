#!/usr/bin/env sh
set -eu

# Legacy strategy tests deliberately create temporary SQLite databases and local
# token files. A VPS candidate supplies PostgreSQL and production token settings;
# leaking those values into this suite makes otherwise independent tests share
# rows. Keep isolation confined to this one-shot process. Production services
# retain PostgreSQL, encrypted credentials and account-scoped execution.
unset DATABASE_URL
export ALLOW_LEGACY_GLOBAL_TOKENS=true
export COPYTRADING_ALLOW_LEGACY_GLOBAL_TOKENS=true
export FRONTEND_ORIGINS="http://127.0.0.1:8080,http://localhost:8080,https://derivadmin.site"

# Production scheduler-v2 keeps browser-direct manual execution and durable server
# scheduling separate: exact-second future schedules clear an old hard-stop fence,
# wake the existing worker, merge scheduled contracts into the canonical Run ledger
# and expose terminal scheduled-session P/L without introducing another BUY path.
node --check dashboard/final-premium-6f3.js
node --check dashboard/final-ui-shell-v2.js
node --check dashboard/vps-api-boundary-v2.js
node --check dashboard/vps-realtime-client-v2.js
node --check dashboard/public-testing-runtime-v1.js
node --check dashboard/direct-pip-precision-v1.js
node --check dashboard/direct-financial-fence-v1.js
node --check dashboard/direct-socket-control-v1.js
node --check dashboard/direct-hard-stop-fence-v1.js
node --check dashboard/direct-reset-authority-v1.js
node --check dashboard/direct-interaction-guard-v3.js
node --check dashboard/deriv-direct-execution-v1.js
node --check dashboard/direct-strategy-persistence-v1.js
node --check dashboard/direct-continuity-checkpoint-v1.js
node --check dashboard/direct-ui-cleanup-v1.js
node --check dashboard/direct-builder-loaded-v2.js
node --check dashboard/direct-runtime-ux-v3.js
node --check dashboard/direct-demo-reset-router-v1.js
node --check dashboard/direct-transaction-ledger-v6.js
node --check dashboard/direct-run-panel-authority-v6.js
node --check dashboard/mobile-layout-authority-v1.js
node --check dashboard/run-panel-usability-v1.js
node --check scripts/export-deriv-quill-icons-v2.mjs
node --check scripts/build-vps.mjs
node --check scripts/build-direct-runtime-v2.mjs
node --check scripts/finalize-direct-runtime-v2.mjs
node --check scripts/finalize-direct-ux-v4.mjs
node --check scripts/finalize-production-controls-v6.mjs
node --check scripts/finalize-production-controls-v6b.mjs
node --check scripts/finalize-scheduler-v2.mjs

python -m py_compile \
  app/direct_execution_hard_stop_state.py \
  app/vps_direct_hard_stop_v2.py \
  app/direct_execution_worker_fence.py \
  app/custom_split_debt_continuity_authority.py \
  app/custom_virtual_post_loss_barrier_authority.py \
  app/vps_direct_execution_checkpoint.py \
  app/automation_scheduler_v2_authority.py \
  app/vps_backend_api.py

exec python -m unittest -q \
  tests.test_scheduler_v2_authority \
  tests.test_run_panel_ledger_v8 \
  tests.test_hybrid_browser_direct_v2 \
  tests.test_persistent_scheduler_action5 \
  tests.test_execution_stop_reason_authority \
  tests.test_custom_execution_consistency_authority \
  tests.test_post_loss_split_and_virtual_neutrality \
  tests.test_clear_trades_unbounded_kpis \
  tests.test_custom_virtual_integrity_authority \
  tests.test_custom_strategy_instant_start \
  tests.test_personal_token_sync \
  tests.test_multi_strategy \
  tests.test_multi_strategy_concurrency \
  tests.test_strategy_v2 \
  tests.test_standardized_execution_runtime \
  tests.test_scalable_group_execution \
  tests.test_rotating_execution_cohorts \
  tests.test_provider_connection_resilience \
  tests.test_websocket_hot_path_hardening \
  tests.test_per_account_virtual_runtime \
  tests.test_strategy_settlement_integrity \
  tests.test_websocket_execution_hardening \
  tests.test_performance_hardening
