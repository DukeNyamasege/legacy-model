#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
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
  compose logs --tail=120 api 2>/dev/null || true
  echo ""
  echo "--- WORKER LOGS ---"
  compose logs --tail=120 worker 2>/dev/null || true
  exit 1
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

echo "============================================================"
echo "SAFE VPS DEPLOYMENT"
echo "============================================================"
echo "Project: $PROJECT_DIR"
echo "Commit : $(git rev-parse HEAD 2>/dev/null || echo unknown)"

echo ""
echo "1. Validate Compose and Python syntax"
compose config --quiet || fail "Docker Compose configuration is invalid."
python3 -m compileall -q app scripts || fail "Python syntax validation failed."

echo ""
echo "2. Pull PostgreSQL and build exact API/worker images"
compose pull database || fail "Failed to pull the PostgreSQL image."
compose build api worker || fail "API/worker image build failed."

echo ""
echo "3. Start PostgreSQL first and preserve its named volume"
compose up -d database || fail "Database container could not start."
wait_for_database_container || fail "PostgreSQL did not become healthy."

echo ""
echo "4. Verify Docker DNS and run migrations once"
compose run --rm --no-deps worker sh -ec '
  python scripts/wait_for_database.py --timeout 180
  alembic upgrade head
' || fail "Database DNS/readiness or Alembic migration failed."

echo ""
echo "5. Replace API and wait until its health endpoint passes"
compose up -d --force-recreate api || fail "API container could not start."
compose up -d --wait --wait-timeout 180 database api || fail "API did not become healthy."

if ! curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
  fail "Local API health endpoint is unavailable."
fi

echo ""
echo "6. Replace worker only after API and database are healthy"
compose up -d --force-recreate worker || fail "Worker container could not start."
sleep 15

WORKER_ID=$(compose ps -q worker)
if [ -z "$WORKER_ID" ]; then
  fail "Worker container was not created."
fi
WORKER_STATE=$(docker inspect -f '{{.State.Status}}' "$WORKER_ID" 2>/dev/null || echo unknown)
WORKER_RESTARTING=$(docker inspect -f '{{.State.Restarting}}' "$WORKER_ID" 2>/dev/null || echo true)
if [ "$WORKER_STATE" != "running" ] || [ "$WORKER_RESTARTING" != "false" ]; then
  fail "Worker is not stably running (state=$WORKER_STATE restarting=$WORKER_RESTARTING)."
fi

if compose logs --since=5m api worker 2>&1 | grep -E 'SyntaxError|ImportError|DATABASE_UNAVAILABLE|Traceback' >/dev/null 2>&1; then
  fail "A fatal startup traceback was detected."
fi

echo ""
echo "============================================================"
echo "VPS DEPLOYMENT PASSED"
echo "============================================================"
compose ps
echo "API health : OK"
echo "Worker     : RUNNING"
echo "Database   : READY"
echo "Named volumes were preserved; docker compose down was not used."
