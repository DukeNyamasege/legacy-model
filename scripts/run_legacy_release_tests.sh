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

# Action 6F-3 remains the base direct-VPS shell. Hybrid browser-direct v2 is the
# current execution/control overlay: one financial live engine, one visible Run
# authority, browser-owned live Deriv execution, persistent scheduler/offline
# takeover, exact demo reset and sticky strategy analysis. Public testing is access
# only and is forbidden from owning Run/Stop. Syntax-check every shipped source.
node --check dashboard/final-premium-6f3.js
node --check dashboard/final-ui-shell-v2.js
node --check dashboard/vps-api-boundary-v2.js
node --check dashboard/vps-realtime-client-v2.js
node --check dashboard/public-testing-runtime-v1.js
node --check dashboard/direct-pip-precision-v1.js
node --check dashboard/direct-financial-fence-v1.js
node --check dashboard/direct-socket-control-v1.js
node --check dashboard/direct-reset-authority-v1.js
node --check dashboard/direct-interaction-guard-v3.js
node --check dashboard/deriv-direct-execution-v1.js
node --check dashboard/direct-strategy-persistence-v1.js
node --check dashboard/direct-continuity-checkpoint-v1.js
node --check dashboard/direct-ui-cleanup-v1.js
node --check dashboard/direct-builder-loaded-v2.js
node --check dashboard/direct-runtime-ux-v3.js
node --check dashboard/direct-demo-reset-router-v1.js
node --check dashboard/direct-run-panel-authority-v5.js
node --check scripts/export-deriv-quill-icons-v2.mjs
node --check scripts/build-vps.mjs
node --check scripts/build-direct-runtime-v2.mjs
node --check scripts/finalize-direct-runtime-v2.mjs
node --check scripts/finalize-direct-ux-v4.mjs

exec python -m unittest -q \
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
