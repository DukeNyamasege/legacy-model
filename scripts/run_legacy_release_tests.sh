#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"

export ALLOW_LEGACY_GLOBAL_TOKENS=true
export COPYTRADING_ALLOW_LEGACY_GLOBAL_TOKENS=true
export FRONTEND_ORIGINS="http://127.0.0.1:8080,http://localhost:8080,https://derivadmin.site"

NODE_CHECK_FILES="
dashboard/final-premium-6f3.js
dashboard/final-ui-shell-v2.js
dashboard/vps-api-boundary-v2.js
dashboard/vps-realtime-client-v2.js
dashboard/public-testing-runtime-v1.js
dashboard/direct-pip-precision-v1.js
dashboard/direct-financial-fence-v1.js
dashboard/direct-socket-control-v1.js
dashboard/direct-hard-stop-fence-v1.js
dashboard/direct-reset-authority-v1.js
dashboard/direct-interaction-guard-v3.js
dashboard/deriv-direct-execution-v1.js
dashboard/direct-strategy-persistence-v1.js
dashboard/direct-continuity-checkpoint-v1.js
dashboard/direct-ui-cleanup-v1.js
dashboard/direct-builder-loaded-v2.js
dashboard/direct-runtime-ux-v3.js
dashboard/direct-demo-reset-router-v1.js
dashboard/direct-transaction-ledger-v6.js
dashboard/direct-run-panel-authority-v6.js
dashboard/mobile-layout-authority-v1.js
dashboard/run-panel-usability-v1.js
scripts/export-deriv-quill-icons-v2.mjs
scripts/build-vps.mjs
scripts/build-direct-runtime-v2.mjs
scripts/finalize-direct-runtime-v2.mjs
scripts/finalize-direct-ux-v4.mjs
scripts/finalize-production-controls-v6.mjs
scripts/finalize-production-controls-v6b.mjs
scripts/finalize-scheduler-v2.mjs
scripts/finalize-execution-continuity-v1.mjs
scripts/finalize-global-recovery-v1.mjs
"

if command -v node >/dev/null 2>&1; then
  for file in $NODE_CHECK_FILES; do
    node --check "$file"
  done
else
  command -v docker >/dev/null 2>&1 || {
    echo "ERROR: neither host Node nor Docker is available for JavaScript syntax checks." >&2
    exit 1
  }
  echo "Host Node not found; using node:22-alpine Docker runtime for syntax checks."
  docker run --rm \
    -e NODE_CHECK_FILES="$NODE_CHECK_FILES" \
    -v "$ROOT_DIR:/work:ro" \
    -w /work \
    node:22-alpine \
    sh -ec 'for file in $NODE_CHECK_FILES; do node --check "$file"; done'
fi

grep -q -- '--camera-bg: #07111f' dashboard/tutorial-camera-theme-v1.css
grep -q -- '--camera-bg: #e9f0f6' dashboard/tutorial-camera-theme-v1.css

command -v docker >/dev/null 2>&1 || {
  echo "ERROR: Docker is required for Python release tests." >&2
  exit 1
}

echo "Building production API test image so Python tests use runtime dependencies."
docker compose -f docker-compose.yml build api

echo "Running Python release tests inside API image."
docker compose -f docker-compose.yml run --rm --no-deps api sh -ec '
  unset DATABASE_URL
  export ALLOW_LEGACY_GLOBAL_TOKENS=true
  export COPYTRADING_ALLOW_LEGACY_GLOBAL_TOKENS=true
  export FRONTEND_ORIGINS="http://127.0.0.1:8080,http://localhost:8080,https://derivadmin.site"

  python -m py_compile \
    app/direct_execution_hard_stop_state.py \
    app/vps_direct_hard_stop_v2.py \
    app/direct_execution_worker_fence.py \
    app/custom_split_debt_continuity_authority.py \
    app/custom_virtual_post_loss_barrier_authority.py \
    app/vps_direct_execution_checkpoint.py \
    app/global_recovery_execution_policy.py \
    app/account_identity_canonical_authority.py \
    app/account_trade_metrics_authority.py \
    app/vps_runtime_policy_hotfix.py \
    app/automation_scheduler_v2_authority.py \
    app/vps_backend_api.py

  python -m unittest -q \
    tests.test_global_recovery_policy \
    tests.test_tutorial_camera_theme \
    tests.test_execution_continuity_v10 \
    tests.test_scheduler_v2_authority \
    tests.test_run_panel_ledger_v8 \
    tests.test_single_global_run_panel \
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
'
