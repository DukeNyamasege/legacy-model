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
API_DATABASE_HEALTHY=false
WORKER_STABLY_RUNNING=false
PRODUCTION_CONTAINERS_RECREATED=false
ACCOUNT_REENROLLMENT_STATUS="NOT REQUESTED"
PREFLIGHT_PROJECT=""
PREFLIGHT_OVERRIDE=""
cd "$PROJECT_DIR"

compose() {
  docker compose -f docker-compose.yml -f docker-compose.vps.yml "$@"
}

candidate_compose() {
  docker compose --project-directory "$PROJECT_DIR" \
    -p "$PREFLIGHT_PROJECT" \
    -f "$PREFLIGHT_OVERRIDE" \
    "$@"
}

cleanup_preflight() {
  if [ -n "$PREFLIGHT_PROJECT" ] && [ -n "$PREFLIGHT_OVERRIDE" ] && [ -f "$PREFLIGHT_OVERRIDE" ]; then
    candidate_compose stop worker api database >/dev/null 2>&1 || true
    candidate_compose rm -f worker api database >/dev/null 2>&1 || true
    docker volume ls -q --filter "label=com.docker.compose.project=$PREFLIGHT_PROJECT" \
      | while IFS= read -r volume_name; do
          [ -n "$volume_name" ] && docker volume rm "$volume_name" >/dev/null 2>&1 || true
        done
    rm -f "$PREFLIGHT_OVERRIDE"
  fi
}

fail() {
  reason=$1
  echo ""
  echo "============================================================"
  echo "VPS DEPLOYMENT FAILED SAFELY"
  echo "============================================================"
  echo "$reason"

  # Never leave an API process claiming to be healthy while PostgreSQL is
  # unavailable. Once the replacement API has passed database health, keep it up
  # on late smoke-test failures so Caddy does not publish a 502 outage.
  if [ "$STACK_STOPPED" = "true" ]; then
    if [ "$API_DATABASE_HEALTHY" = "true" ]; then
      echo "API passed database health before this failure; leaving it running to avoid a public 502."
      if [ "$WORKER_STABLY_RUNNING" = "true" ]; then
        echo "Worker passed startup checks before this failure; leaving it running."
      else
        compose stop worker >/dev/null 2>&1 || true
      fi
    elif [ "$PRODUCTION_CONTAINERS_RECREATED" != "true" ]; then
      echo "Production cutover failed before containers were replaced; restarting the previous api/worker."
      compose start api worker >/dev/null 2>&1 || true
    else
      compose stop worker api >/dev/null 2>&1 || true
    fi
  fi

  if [ -n "$PREFLIGHT_PROJECT" ] && [ -n "$PREFLIGHT_OVERRIDE" ] && [ -f "$PREFLIGHT_OVERRIDE" ]; then
    echo ""
    echo "--- RELEASE GATE API LOGS ---"
    candidate_compose logs --tail=180 api 2>/dev/null || true
    echo ""
    echo "--- RELEASE GATE WORKER LOGS ---"
    candidate_compose logs --tail=220 worker 2>/dev/null || true
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
  cleanup_preflight
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

write_preflight_override() {
  PREFLIGHT_OVERRIDE="$STATE_DIR/preflight-compose-$$.yml"
  cat > "$PREFLIGHT_OVERRIDE" <<EOF
services:
  database:
    image: postgres:17-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: \${POSTGRES_DB:-underdog_test2}
      POSTGRES_USER: \${POSTGRES_USER:-underdog}
      POSTGRES_PASSWORD: \${POSTGRES_PASSWORD}
    volumes:
      - test2_database:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \${POSTGRES_USER:-underdog} -d \${POSTGRES_DB:-underdog_test2}"]
      interval: 5s
      timeout: 5s
      retries: 24
      start_period: 5s
    networks:
      test2: {}

  api:
    build:
      context: .
      target: api
    restart: unless-stopped
    command: >-
      sh -ec "python scripts/wait_for_database.py --timeout 180
      && alembic upgrade head
      && exec uvicorn app.api_v3:app --host 0.0.0.0 --port 8080"
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg://\${POSTGRES_USER:-underdog}:\${POSTGRES_PASSWORD}@database:5432/\${POSTGRES_DB:-underdog_test2}
      DEPLOYMENT_ID: preflight-api
      DERIV_ENVIRONMENT: demo
      DERIV_TRADING_ENABLED: "false"
      TRADING_MODE: demo
      ALLOW_REAL_TRADING: "false"
      PRODUCTION_ACKNOWLEDGEMENT: ""
      TELEGRAM_ALERTS_ENABLED: "false"
      TELEGRAM_NOTIFICATIONS_SUSPENDED: "true"
      DEPLOYMENT_RELEASE_ID: ""
      DEPLOYMENT_RELEASE_FROM: ""
      DEPLOYMENT_RELEASE_CHANGE_COUNT: "0"
      DEPLOYMENT_RELEASE_MESSAGE_B64: ""
    depends_on:
      database:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/database', timeout=3).read()"]
      interval: 5s
      timeout: 5s
      retries: 24
      start_period: 20s
    networks:
      test2: {}

  worker:
    build:
      context: .
      target: worker
    restart: unless-stopped
    command: >-
      sh -ec "python scripts/wait_for_database.py --timeout 180
      && exec python -m app.worker"
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg://\${POSTGRES_USER:-underdog}:\${POSTGRES_PASSWORD}@database:5432/\${POSTGRES_DB:-underdog_test2}
      DEPLOYMENT_ID: preflight-worker
      DERIV_ENVIRONMENT: demo
      DERIV_TRADING_ENABLED: "false"
      TRADING_MODE: demo
      ALLOW_REAL_TRADING: "false"
      PRODUCTION_ACKNOWLEDGEMENT: ""
      TELEGRAM_ALERTS_ENABLED: "false"
      TELEGRAM_NOTIFICATIONS_SUSPENDED: "true"
      INTERNAL_DASHBOARD_REFRESH_URL: http://api:8080/control/internal/dashboard-settlement-refresh
      DEPLOYMENT_RELEASE_ID: ""
      DEPLOYMENT_RELEASE_FROM: ""
      DEPLOYMENT_RELEASE_CHANGE_COUNT: "0"
      DEPLOYMENT_RELEASE_MESSAGE_B64: ""
    volumes:
      - test2_models:/app/model_artifacts
    depends_on:
      database:
        condition: service_healthy
      api:
        condition: service_healthy
    networks:
      test2: {}

volumes:
  test2_database:
  test2_models:

networks:
  test2:
    internal: false
EOF
}

run_release_gate() {
  short_commit=$(printf '%s' "$CURRENT_COMMIT" | cut -c1-12)
  PREFLIGHT_PROJECT="legacy-model-preflight-$short_commit"
  write_preflight_override
  echo "Candidate project: $PREFLIGHT_PROJECT"
  echo "Candidate API    : internal api:8080 only; no host port is published"
  echo "Live production  : remains on the existing api/worker until this gate passes"

  cleanup_preflight
  write_preflight_override
  preflight_started_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  candidate_compose config --quiet || {
    candidate_compose config 2>&1 || true
    fail "Release gate Compose configuration is invalid."
  }
  candidate_compose build api worker || fail "Release gate image build failed."
  candidate_compose up -d --force-recreate --wait --wait-timeout 180 database \
    || fail "Release gate PostgreSQL did not become healthy."
  candidate_compose run --rm --no-deps worker sh -ec '
    python scripts/wait_for_database.py --timeout 180
    alembic upgrade head
  ' || fail "Release gate migration check failed."

  echo "Release gate: verify AIDR and private WebSocket behavior against isolated PostgreSQL"
  candidate_compose run --rm --no-deps worker sh -ec '
    python -m unittest -q \
      test_custom_martingale.py \
      test_aidr_recovery_v2.py \
      tests.test_independent_websocket_execution \
      tests.test_private_websocket_credentials
  ' || fail "Release gate AIDR or private WebSocket tests failed. Production was not changed."

  echo "Release gate: verify legacy strategy behavior in per-test local databases"
  candidate_compose run --rm --no-deps worker sh scripts/run_legacy_release_tests.sh \
    || fail "Release gate legacy strategy tests failed. Production was not changed."

  candidate_compose up -d --force-recreate --wait --wait-timeout 180 api worker \
    || fail "Release gate API/worker did not become healthy."
  candidate_compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/database', timeout=5).read()" \
    || fail "Release gate candidate database health endpoint is unavailable inside the candidate API."
  candidate_compose exec -T api python scripts/production_smoke.py \
    --base-url http://127.0.0.1:8080 \
    --ready-timeout 180 \
    || fail "Release gate smoke test failed. Production was not changed."

  PREFLIGHT_FATAL_LOG_MATCHES=$(candidate_compose logs --since="$preflight_started_at" api worker 2>&1 \
    | grep -E 'SyntaxError|ImportError|DATABASE_UNAVAILABLE|DATABASE_REQUEST_UNAVAILABLE|Traceback|MODE_AWARE_DASHBOARD_BROADCAST_FAILED|DASHBOARD_SETTLEMENT_PUSH_DISABLED' \
    || true)
  if [ -n "$PREFLIGHT_FATAL_LOG_MATCHES" ]; then
    echo "--- RELEASE GATE FATAL LOG MATCHES ---"
    printf '%s\n' "$PREFLIGHT_FATAL_LOG_MATCHES"
    fail "Release gate found fatal startup or integration logs. Production was not changed."
  fi

  cleanup_preflight
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
sh -n scripts/deploy_vps.sh scripts/update_vps.sh scripts/run_legacy_release_tests.sh \
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

echo ""
echo "3. Gate the release in an isolated candidate stack before touching production"
run_release_gate

echo ""
echo "4. Verify live PostgreSQL and create a pre-migration backup before cutover"
if [ -z "$(compose ps --status running -q database 2>/dev/null || true)" ]; then
  compose up -d database || fail "Production PostgreSQL container could not be ensured."
fi
wait_for_database_container || fail "Production PostgreSQL is not healthy; live services were not changed."
assert_database_container_integrity || fail "Production PostgreSQL integrity validation failed; live services were not changed."
backup_database || fail "Pre-migration PostgreSQL backup failed; live services were not changed."

# Stop both request handling and execution only after replacement images and the
# isolated candidate stack have passed. Until this line, the live platform keeps
# using the previous api/worker.
echo ""
echo "5. Stop old API and worker only after the release gate passes"
compose stop worker api || true
STACK_STOPPED=true

echo ""
echo "6. Verify Docker DNS and run production migrations once"
compose run --rm --no-deps worker sh -ec '
  python scripts/wait_for_database.py --timeout 180
  alembic upgrade head
' || fail "Database DNS/readiness or Alembic migration failed."

echo ""
echo "7. Replace API and require database-aware health"
PRODUCTION_CONTAINERS_RECREATED=true
compose up -d --force-recreate --no-deps api || fail "API container could not start."
compose up -d --wait --wait-timeout 180 api || fail "API/database did not become healthy."

if ! curl -fsS http://127.0.0.1:8080/health/database >/dev/null 2>&1; then
  fail "API database-health endpoint is unavailable."
fi
API_DATABASE_HEALTHY=true

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
WORKER_STABLY_RUNNING=true

echo ""
echo "9. Run full backend, OAuth, provider, dashboard and WebSocket smoke tests"
compose exec -T api python scripts/production_smoke.py \
  --base-url http://127.0.0.1:8080 \
  --ready-timeout 120 \
  || fail "Production integration smoke test failed."

echo ""
echo "10. Reject fatal errors generated by this deployment only"
FATAL_LOG_MATCHES=$(compose logs --since="$DEPLOY_STARTED_AT" api worker 2>&1 \
  | grep -E 'SyntaxError|ImportError|DATABASE_UNAVAILABLE|DATABASE_REQUEST_UNAVAILABLE|Traceback|MODE_AWARE_DASHBOARD_BROADCAST_FAILED|DASHBOARD_SETTLEMENT_PUSH_DISABLED' \
  || true)
if [ -n "$FATAL_LOG_MATCHES" ]; then
  echo "--- FATAL LOG MATCHES ---"
  printf '%s\n' "$FATAL_LOG_MATCHES"
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
