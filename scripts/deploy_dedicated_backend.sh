#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BACKUP_DIR="$PROJECT_DIR/deploy-backups"
cd "$PROJECT_DIR"

compose() {
  docker compose -f docker-compose.yml "$@"
}

fail() {
  echo ""
  echo "CONTABO BACKEND DEPLOYMENT FAILED: $1" >&2
  compose ps || true
  exit 1
}

[ -f .env ] || fail "Missing $PROJECT_DIR/.env. Create it from .env.vps.example before deployment."

printf '%s\n' "============================================================"
printf '%s\n' "NETLIFY + CONTABO BACKEND DEPLOYMENT"
printf '%s\n' "============================================================"
printf 'Project : %s\n' "$PROJECT_DIR"
printf 'Commit  : %s\n' "$(git rev-parse HEAD)"

printf '%s\n' "1. Validate backend-only Compose and source"
compose config --quiet || fail "Docker Compose configuration is invalid."
python3 -m compileall -q app scripts || fail "Python syntax validation failed."

printf '%s\n' "2. Build replacement API and worker before changing running containers"
compose build api worker || fail "API/worker image build failed."

printf '%s\n' "3. Start/verify PostgreSQL without replacing its named volume"
compose up -d database || fail "PostgreSQL could not be started."
attempt=0
while [ "$attempt" -lt 60 ]; do
  attempt=$((attempt + 1))
  if compose exec -T database sh -ec 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    break
  fi
  [ "$attempt" -lt 60 ] || fail "PostgreSQL did not become ready."
  sleep 2
done

printf '%s\n' "4. Create a pre-deploy PostgreSQL backup when an existing database is present"
mkdir -p "$BACKUP_DIR"
timestamp=$(date -u +"%Y%m%dT%H%M%SZ")
short_commit=$(git rev-parse --short=12 HEAD)
backup="$BACKUP_DIR/predeploy_${timestamp}_${short_commit}.dump"
if compose exec -T database sh -ec '
  pg_dump --format=custom --no-owner --no-privileges \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB"
' > "$backup"; then
  chmod 600 "$backup"
  echo "DATABASE_BACKUP_CREATED file=$backup"
else
  rm -f "$backup"
  fail "Database backup failed; deployment stopped before API/worker cutover."
fi

printf '%s\n' "5. Run migrations using the candidate API image"
compose run --rm --no-deps api sh -ec '
  python scripts/wait_for_database.py --timeout 180
  alembic upgrade head
' || fail "Database migration failed."

printf '%s\n' "6. Recreate backend API"
compose up -d --force-recreate --remove-orphans --no-deps api \
  || fail "API cutover failed."

printf '%s\n' "7. Wait for backend health"
attempt=0
while [ "$attempt" -lt 60 ]; do
  attempt=$((attempt + 1))
  if curl -fsS --max-time 3 http://127.0.0.1:8080/health/database >/dev/null 2>&1; then
    break
  fi
  [ "$attempt" -lt 60 ] || fail "Backend database health endpoint did not become ready."
  sleep 2
done

curl -fsS --max-time 5 http://127.0.0.1:8080/health >/dev/null \
  || fail "Backend liveness endpoint failed."
curl -fsS --max-time 5 http://127.0.0.1:8080/health/frontend-backend >/dev/null \
  || fail "Netlify/backend architecture health endpoint failed."

printf '%s\n' "8. Recreate worker"
compose up -d --force-recreate --remove-orphans --no-deps worker \
  || fail "Worker cutover failed."

printf '%s\n' "9. Final service state"
compose ps

printf '%s\n' "============================================================"
printf '%s\n' "CONTABO BACKEND DEPLOYMENT PASSED"
printf '%s\n' "Frontend: Netlify"
printf '%s\n' "Contabo role: API + worker + PostgreSQL only"
printf '%s\n' "PostgreSQL named volume preserved"
printf '%s\n' "============================================================"
