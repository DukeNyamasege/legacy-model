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

exec python -m unittest -q \
  tests.test_multi_strategy \
  tests.test_multi_strategy_concurrency \
  tests.test_strategy_v2 \
  tests.test_websocket_execution_hardening \
  tests.test_generated_multi_strategy_js \
  tests.test_performance_hardening \
  tests.test_generated_request_broker_js \
  test_rf_dir5.py \
  test_strategy_logic.py
