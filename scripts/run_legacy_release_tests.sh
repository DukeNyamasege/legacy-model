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
export FRONTEND_ORIGINS="http://127.0.0.1:8080,http://localhost:8080,https://derivadmin.site,https://legacymodel.netlify.app"

# These dashboard guards are part of the execution/editing safety surface. A
# malformed script must fail the same release gate that validates the worker.
node --check dashboard/execution-status-banner.js
node --check dashboard/builder-edit-stability.js
node --check dashboard/runtime-ux-authority.js
node --check dashboard/virtual-kpi-neutrality.js
node --check dashboard/automation-home-v1.js
node --check dashboard/text-to-strategy-v1.js
node --check dashboard/strategy-ready-v1.js
node --check dashboard/timezone-schedule-v1.js
node --check dashboard/timezone-home-sync-v1.js
node --check dashboard/prelogin-landing-v2.js

exec python -m unittest -q \
  tests.test_text_to_strategy_action2 \
  tests.test_strategy_ready_action3 \
  tests.test_timezone_schedule_action4 \
  tests.test_execution_stop_reason_authority \
  tests.test_custom_execution_consistency_authority \
  tests.test_post_loss_split_and_virtual_neutrality \
  tests.test_clear_trades_unbounded_kpis \
  tests.test_builder_edit_stability \
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
  tests.test_generated_multi_strategy_js \
  tests.test_performance_hardening \
  tests.test_generated_request_broker_js \
  test_rf_dir5.py \
  test_strategy_logic.py
