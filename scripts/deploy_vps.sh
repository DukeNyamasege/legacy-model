#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
STATE_DIR="$PROJECT_DIR/.deployment_state"
LAST_SUCCESSFUL_COMMIT_FILE="$STATE_DIR/last_successful_commit"
cd "$PROJECT_DIR"

compose() {
  docker compose -f docker-compose.yml -f docker-compose.vps.yml "$@"
}

fail() {
  echo ""
  echo "============================================================"
  echo "VPS DEPLOYMENT FAILED SAFELY"
  echo "============================================================"
  echo "$1"
  compose stop worker >/dev/null 2>&1 || true
  compose ps || true
  echo ""
  echo "--- API LOGS ---"
  compose logs --tail=160 api 2>/dev/null || true
  echo ""
  echo "--- WORKER LOGS ---"
  compose logs --tail=200 worker 2>/dev/null || true
  exit 1
}

valid_commit() {
  [ -n "${1:-}" ] && git cat-file -e "$1^{commit}" >/dev/null 2>&1
}

wait_for_database_container() {
  attempts=0
  while [ "$attempts" -lt 60 ]; do
    attempts=$((attempts + 1))
    if compose exec -T database sh -ec 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
      echo "DATABASE_CONTAINER_READY attempts=$attempts"
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_for_telegram_release() {
  short_release=$(printf '%s' "$CURRENT_COMMIT" | cut -c1-12)
  attempts=0
  while [ "$attempts" -lt 30 ]; do
    attempts=$((attempts + 1))
    logs=$(compose logs --since=5m worker 2>&1 || true)
    if printf '%s' "$logs" | grep -F "TELEGRAM_DEPLOYMENT_RELEASE_SENT release_id=$short_release" >/dev/null 2>&1; then
      return 0
    fi
    if printf '%s' "$logs" | grep -F "TELEGRAM_DEPLOYMENT_RELEASE_FAILED release_id=$short_release" >/dev/null 2>&1; then
      return 2
    fi
    sleep 1
  done
  return 1
}

mkdir -p "$STATE_DIR"
CURRENT_COMMIT=$(git rev-parse HEAD)
PREVIOUS_COMMIT=${DEPLOY_PREVIOUS_COMMIT:-}

if ! valid_commit "$PREVIOUS_COMMIT" && [ -f "$LAST_SUCCESSFUL_COMMIT_FILE" ]; then
  PREVIOUS_COMMIT=$(sed -n '1p' "$LAST_SUCCESSFUL_COMMIT_FILE" | tr -d '[:space:]')
fi
if ! valid_commit "$PREVIOUS_COMMIT"; then
  PREVIOUS_COMMIT=$(git rev-parse HEAD^ 2>/dev/null || printf '%s' "$CURRENT_COMMIT")
fi
if ! git merge-base --is-ancestor "$PREVIOUS_COMMIT" "$CURRENT_COMMIT" >/dev/null 2>&1; then
  PREVIOUS_COMMIT=$(git rev-parse HEAD^ 2>/dev/null || printf '%s' "$CURRENT_COMMIT")
fi

RELEASE_EXPORTS=$(python3 scripts/generate_deployment_release.py \
  --from-commit "$PREVIOUS_COMMIT" \
  --to-commit "$CURRENT_COMMIT" \
  --shell) || fail "Could not generate the deployment release summary."
eval "$RELEASE_EXPORTS"
export DEPLOYMENT_RELEASE_ID
export DEPLOYMENT_RELEASE_FROM
export DEPLOYMENT_RELEASE_CHANGE_COUNT
export DEPLOYMENT_RELEASE_MESSAGE_B64

echo "============================================================"
echo "SAFE VPS DEPLOYMENT"
echo "============================================================"
echo "Project : $PROJECT_DIR"
echo "Previous: $PREVIOUS_COMMIT"
echo "Target  : $CURRENT_COMMIT"
echo "Changes : $DEPLOYMENT_RELEASE_CHANGE_COUNT"

if [ "$DEPLOYMENT_RELEASE_CHANGE_COUNT" -gt 0 ]; then
  echo ""
  echo "Telegram deployment update preview:"
  python3 scripts/generate_deployment_release.py \
    --from-commit "$PREVIOUS_COMMIT" \
    --to-commit "$CURRENT_COMMIT"
else
  echo "No new commits were detected; no Telegram deployment announcement will be sent."
fi

echo ""
echo "1. Validate Compose, Python and dashboard JavaScript syntax"
compose config --quiet || fail "Docker Compose configuration is invalid."
python3 -m compileall -q app scripts || fail "Python syntax validation failed."
if command -v node >/dev/null 2>&1; then
  node --check dashboard/realtime-mode-hardening.js \
    || fail "Realtime dashboard JavaScript syntax validation failed."
fi

echo ""
echo "2. Pull PostgreSQL and build exact API/worker images"
compose pull database || fail "Failed to pull the PostgreSQL image."
compose build api worker || fail "API/worker image build failed."

# Stop the old execution process only after the replacement images have passed
# Compose, syntax, and image-build validation. This prevents duplicate execution
# during migrations without taking the working bot down for a failed build.
echo ""
echo "3. Stop old worker after replacement images validate"
compose stop worker || true

echo ""
echo "4. Start PostgreSQL first and preserve its named volume"
compose up -d database || fail "Database container could not start."
wait_for_database_container || fail "PostgreSQL did not become healthy."

echo ""
echo "5. Verify Docker DNS and run migrations once"
compose run --rm --no-deps worker sh -ec '
  python scripts/wait_for_database.py --timeout 180
  alembic upgrade head
' || fail "Database DNS/readiness or Alembic migration failed."

echo ""
echo "6. Replace API and wait until its liveness endpoint passes"
compose up -d --force-recreate api || fail "API container could not start."
compose up -d --wait --wait-timeout 180 database api || fail "API did not become healthy."

if ! curl -fsS http://127.0.0.1:8080/health/live >/dev/null 2>&1; then
  fail "Local API liveness endpoint is unavailable."
fi

echo ""
echo "7. Replace worker only after API and database are healthy"
compose up -d --force-recreate worker || fail "Worker container could not start."
sleep 8

WORKER_ID=$(compose ps -q worker)
if [ -z "$WORKER_ID" ]; then
  fail "Worker container was not created."
fi
WORKER_STATE=$(docker inspect -f '{{.State.Status}}' "$WORKER_ID" 2>/dev/null || echo unknown)
WORKER_RESTARTING=$(docker inspect -f '{{.State.Restarting}}' "$WORKER_ID" 2>/dev/null || echo true)
if [ "$WORKER_STATE" != "running" ] || [ "$WORKER_RESTARTING" != "false" ]; then
  fail "Worker is not stably running (state=$WORKER_STATE restarting=$WORKER_RESTARTING)."
fi

echo ""
echo "8. Run full backend, OAuth, provider, dashboard and WebSocket smoke tests"
compose exec -T api python scripts/production_smoke.py \
  --base-url http://127.0.0.1:8080 \
  --ready-timeout 120 \
  || fail "Production integration smoke test failed."

echo ""
echo "9. Reject fatal startup and integration log errors"
if compose logs --since=10m api worker 2>&1 \
  | grep -E 'SyntaxError|ImportError|DATABASE_UNAVAILABLE|Traceback|MODE_AWARE_DASHBOARD_BROADCAST_FAILED|DASHBOARD_SETTLEMENT_PUSH_DISABLED' \
  >/dev/null 2>&1; then
  fail "A fatal startup or integration traceback was detected."
fi

TELEGRAM_RELEASE_STATUS="NOT REQUIRED"
if [ "$DEPLOYMENT_RELEASE_CHANGE_COUNT" -gt 0 ]; then
  echo ""
  echo "10. Authorize the queued Telegram release note after all checks passed"
  if compose exec -T api python scripts/mark_deployment_release_ready.py \
    --release-id "$CURRENT_COMMIT"; then
    if wait_for_telegram_release; then
      TELEGRAM_RELEASE_STATUS="SENT"
    else
      release_result=$?
      if [ "$release_result" -eq 2 ]; then
        TELEGRAM_RELEASE_STATUS="FAILED — it remains ready and will retry on the next worker start"
      else
        TELEGRAM_RELEASE_STATUS="PENDING — check worker logs for Telegram channel discovery"
      fi
    fi
  else
    TELEGRAM_RELEASE_STATUS="FAILED — deployment passed but the release could not be authorized"
  fi
fi

printf '%s\n' "$CURRENT_COMMIT" > "$LAST_SUCCESSFUL_COMMIT_FILE"

echo ""
echo "============================================================"
echo "VPS DEPLOYMENT PASSED"
echo "============================================================"
compose ps
echo "API liveness        : OK"
echo "API/worker readiness: OK"
echo "OAuth + PKCE         : OK"
echo "Deriv public WS      : OK"
echo "Demo dashboard       : OK"
echo "Real dashboard       : OK"
echo "Dashboard WebSocket  : OK"
echo "Telegram release note: $TELEGRAM_RELEASE_STATUS"
echo "Worker               : RUNNING"
echo "Database             : READY"
echo "Named volumes and trading history were preserved; no reset was performed."
