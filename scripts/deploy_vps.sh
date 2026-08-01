#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
STATE_DIR="$PROJECT_DIR/.deployment_state"
LAST_SUCCESSFUL_COMMIT_FILE="$STATE_DIR/last_successful_commit"
PENDING_FROM_COMMIT_FILE="$STATE_DIR/pending_from_commit"
BACKUP_DIR="$PROJECT_DIR/deploy-backups"
DEPLOY_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
STACK_STOPPED=false
ACCOUNT_REENROLLMENT_STATUS="NOT REQUESTED"
cd "$PROJECT_DIR"

compose() {
  docker compose -f docker-compose.yml -f docker-compose.vps.yml "$@"
}

fail() {
  reason=$1
  echo ""
  echo "============================================================"
  echo "VPS DEPLOYMENT FAILED SAFELY"
  echo "============================================================"
  echo "$reason"

  # Never leave an API process claiming to be healthy while PostgreSQL or the
  # worker is unavailable. The database volume is deliberately left untouched.
  if [ "$STACK_STOPPED" = "true" ]; then
    compose stop worker api >/dev/null 2>&1 || true
  fi

  compose ps || true
  echo ""
  echo "--- DATABASE LOGS ---"
  compose logs --tail=120 database 2>/dev/null || true
  echo ""
  echo "--- API LOGS ---"
  compose logs --tail=180 api 2>/dev/null || true
  echo ""
  echo "--- WORKER LOGS ---"
  compose logs --tail=220 worker 2>/dev/null || true
  echo ""
  echo "The PostgreSQL named volume was preserved."
  echo "The release comparison base remains in: $PENDING_FROM_COMMIT_FILE"
  exit 1
}

valid_commit() {
  [ -n "${1:-}" ] && git cat-file -e "$1^{commit}" >/dev/null 2>&1
}

write_state_file() {
  destination=$1
  value=$2
  temporary="${destination}.tmp"
  printf '%s\n' "$value" > "$temporary"
  mv "$temporary" "$destination"
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

assert_database_container_integrity() {
  database_id=$(compose ps -q database)
  [ -n "$database_id" ] || {
    echo "Database container ID is missing." >&2
    return 1
  }

  networks=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$database_id" 2>/dev/null || true)
  [ -n "$(printf '%s' "$networks" | tr -d '[:space:]')" ] || {
    echo "Database container is not attached to a Docker network." >&2
    return 1
  }

  data_mount=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{if .Name}}{{.Name}}{{else}}{{.Source}}{{end}}{{end}}{{end}}' "$database_id" 2>/dev/null || true)
  [ -n "$data_mount" ] || {
    echo "Database container has no /var/lib/postgresql/data mount." >&2
    return 1
  }

  echo "DATABASE_CONTAINER_INTEGRITY networks=$networks data_mount=$data_mount"
}

recreate_database_container() {
  # Removing a Compose container does not remove its named volume unless -v is
  # explicitly supplied. This repairs stale/missing network attachments while
  # preserving every PostgreSQL row.
  compose stop database >/dev/null 2>&1 || true
  compose rm -f database >/dev/null 2>&1 || true
  compose up -d --force-recreate --no-deps database
}

backup_database() {
  mkdir -p "$BACKUP_DIR"
  timestamp=$(date -u +"%Y%m%dT%H%M%SZ")
  short_commit=$(printf '%s' "$CURRENT_COMMIT" | cut -c1-12)
  backup_file="$BACKUP_DIR/predeploy_${timestamp}_${short_commit}.dump"

  if compose exec -T database sh -ec '
    pg_dump --format=custom --no-owner --no-privileges \
      -U "$POSTGRES_USER" -d "$POSTGRES_DB"
  ' > "$backup_file"; then
    chmod 600 "$backup_file"
    echo "DATABASE_BACKUP_CREATED file=$backup_file"
    find "$BACKUP_DIR" -type f -name 'predeploy_*.dump' -mtime +14 -delete 2>/dev/null || true
    return 0
  fi

  rm -f "$backup_file"
  return 1
}

maybe_reset_account_enrollment() {
  flag=$(printf '%s' "${DEPLOY_RESET_ACCOUNT_ENROLLMENT:-false}" | tr '[:upper:]' '[:lower:]')
  case "$flag" in
    1|true|yes)
      echo ""
      echo "8a. Archive existing trader registrations before worker startup"
      compose run --rm --no-deps api python scripts/reset_account_enrollment.py \
        --confirm RESET_ALL_AUTO_TRADERS \
        --deployment-id "$CURRENT_COMMIT" \
        --allow-open-trades \
        || return 1
      ACCOUNT_REENROLLMENT_STATUS="RESET APPLIED"
      ;;
    *)
      ACCOUNT_REENROLLMENT_STATUS="NOT REQUESTED"
      ;;
  esac
  return 0
}

wait_for_telegram_release() {
  short_release=$(printf '%s' "$CURRENT_COMMIT" | cut -c1-12)
  attempts=0
  while [ "$attempts" -lt 30 ]; do
    attempts=$((attempts + 1))
    logs=$(compose logs --since="$DEPLOY_STARTED_AT" worker 2>&1 || true)
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
if ! valid_commit "$PREVIOUS_COMMIT" && [ -f "$PENDING_FROM_COMMIT_FILE" ]; then
  PREVIOUS_COMMIT=$(sed -n '1p' "$PENDING_FROM_COMMIT_FILE" | tr -d '[:space:]')
fi
if ! valid_commit "$PREVIOUS_COMMIT"; then
  PREVIOUS_COMMIT=$CURRENT_COMMIT
fi
if ! git merge-base --is-ancestor "$PREVIOUS_COMMIT" "$CURRENT_COMMIT" >/dev/null 2>&1; then
  echo "WARNING: Stored deployment base is not an ancestor of the target; no release comparison will be invented."
  PREVIOUS_COMMIT=$CURRENT_COMMIT
fi
write_state_file "$PENDING_FROM_COMMIT_FILE" "$PREVIOUS_COMMIT"

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
  echo "Telegram deployment update preview (PREVIEW ONLY — sent after every check passes):"
  python3 scripts/generate_deployment_release.py \
    --from-commit "$PREVIOUS_COMMIT" \
    --to-commit "$CURRENT_COMMIT"
else
  echo "No new commits were detected; no Telegram deployment announcement will be sent."
fi

echo ""
echo "1. Validate shell, Compose, Python and dashboard JavaScript syntax"
sh -n scripts/deploy_vps.sh scripts/update_vps.sh \
  || fail "Deployment shell syntax validation failed."
compose config --quiet || fail "Docker Compose configuration is invalid."
python3 -m compileall -q app scripts || fail "Python syntax validation failed."
if command -v node >/dev/null 2>&1; then
  node --check dashboard/realtime-mode-hardening.js \
    || fail "Realtime dashboard JavaScript syntax validation failed."
  node --check dashboard/custom-martingale.js \
    || fail "Custom Martingale JavaScript syntax validation failed."
fi

echo ""
echo "2. Pull PostgreSQL and build exact API/worker images"
compose pull database || fail "Failed to pull the PostgreSQL image."
compose build api worker || fail "API/worker image build failed."

# Run account-level stake tests inside the exact worker image before the live API
# or worker is stopped. A failed System/Custom/Flat calculation leaves the current
# production deployment untouched.
echo ""
echo "2a. Verify System and Custom Martingale stake calculations"
compose run --rm --no-deps worker \
  python -m unittest -q test_custom_martingale.py \
  || fail "Custom Martingale unit tests failed."

# Stop both request handling and execution only after replacement images have
# passed every static/build validation. This prevents database-outage tracebacks
# and guarantees that no old worker can trade during migration.
echo ""
echo "3. Stop old API and worker after replacement images validate"
compose stop worker api || true
STACK_STOPPED=true

echo ""
echo "4. Recreate only the PostgreSQL container and repair its Compose network"
recreate_database_container || fail "Database container/network recreation failed."
wait_for_database_container || fail "PostgreSQL did not become healthy."
assert_database_container_integrity || fail "PostgreSQL container integrity validation failed."

echo ""
echo "5. Create a pre-migration PostgreSQL backup"
backup_database || fail "Pre-migration PostgreSQL backup failed."

echo ""
echo "6. Verify Docker DNS and run migrations once"
compose run --rm --no-deps worker sh -ec '
  python scripts/wait_for_database.py --timeout 180
  alembic upgrade head
' || fail "Database DNS/readiness or Alembic migration failed."

echo ""
echo "7. Replace API and require database-aware health"
compose up -d --force-recreate --no-deps api || fail "API container could not start."
compose up -d --wait --wait-timeout 180 database api || fail "API/database did not become healthy."

if ! curl -fsS http://127.0.0.1:8080/health/database >/dev/null 2>&1; then
  fail "API database-health endpoint is unavailable."
fi

maybe_reset_account_enrollment || fail "Account re-enrollment reset failed."

echo ""
echo "8. Replace worker only after API and PostgreSQL are healthy"
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
echo "9. Run full backend, OAuth, provider, dashboard and WebSocket smoke tests"
compose exec -T api python scripts/production_smoke.py \
  --base-url http://127.0.0.1:8080 \
  --ready-timeout 120 \
  || fail "Production integration smoke test failed."

echo ""
echo "10. Reject fatal errors generated by this deployment only"
if compose logs --since="$DEPLOY_STARTED_AT" api worker 2>&1 \
  | grep -E 'SyntaxError|ImportError|DATABASE_UNAVAILABLE|DATABASE_REQUEST_UNAVAILABLE|Traceback|MODE_AWARE_DASHBOARD_BROADCAST_FAILED|DASHBOARD_SETTLEMENT_PUSH_DISABLED|DASHBOARD_SETTLEMENT_PUSH_FAILED' \
  >/dev/null 2>&1; then
  fail "A fatal startup or integration error was detected in this deployment."
fi

TELEGRAM_RELEASE_STATUS="NOT REQUIRED"
if [ "$DEPLOYMENT_RELEASE_CHANGE_COUNT" -gt 0 ]; then
  echo ""
  echo "11. Authorize the queued Telegram release note after all checks passed"
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

write_state_file "$LAST_SUCCESSFUL_COMMIT_FILE" "$CURRENT_COMMIT"
rm -f "$PENDING_FROM_COMMIT_FILE"
STACK_STOPPED=false

echo ""
echo "============================================================"
echo "VPS DEPLOYMENT PASSED"
echo "============================================================"
compose ps
echo "API liveness         : OK"
echo "API database health  : OK"
echo "API/worker readiness : OK"
echo "OAuth + PKCE          : OK"
echo "Deriv public WS       : OK"
echo "Demo dashboard        : OK"
echo "Real dashboard        : OK"
echo "Dashboard WebSocket   : OK"
echo "Custom Martingale     : VERIFIED"
echo "Telegram release note : $TELEGRAM_RELEASE_STATUS"
echo "Account enrollment    : $ACCOUNT_REENROLLMENT_STATUS"
echo "Worker                : RUNNING"
echo "Database              : READY"
echo "Pre-migration backup  : CREATED"
echo "Named volumes and trading history were preserved. Account enrollment status: $ACCOUNT_REENROLLMENT_STATUS"