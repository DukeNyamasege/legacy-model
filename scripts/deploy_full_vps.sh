#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BACKUP_DIR="$PROJECT_DIR/deploy-backups"
PUBLIC_ORIGIN=${PUBLIC_ORIGIN:-https://derivadmin.site}
cd "$PROJECT_DIR"

compose() {
  PUBLIC_ORIGIN="$PUBLIC_ORIGIN" docker compose \
    -f docker-compose.yml \
    -f docker-compose.vps.yml \
    "$@"
}

fail() {
  echo "" >&2
  echo "FULL VPS DEPLOYMENT FAILED: $1" >&2
  compose ps || true
  exit 1
}

[ -f .env ] || fail "Missing $PROJECT_DIR/.env. Create it from .env.vps.example before deployment."
command -v docker >/dev/null 2>&1 || fail "Docker is not installed."
command -v curl >/dev/null 2>&1 || fail "curl is not installed."

printf '%s\n' "============================================================"
printf '%s\n' "FULL CONTABO VPS DEPLOYMENT"
printf '%s\n' "============================================================"
printf 'Project       : %s\n' "$PROJECT_DIR"
printf 'Commit        : %s\n' "$(git rev-parse HEAD)"
printf 'Public origin : %s\n' "$PUBLIC_ORIGIN"
printf '%s\n' "Architecture  : frontend + API + WebSocket + worker + PostgreSQL"

printf '%s\n' "1. Validate full-VPS Compose and source"
compose config --quiet || fail "Docker Compose configuration is invalid."
python3 -m compileall -q app scripts || fail "Python syntax validation failed."
sh -n scripts/prepare_full_vps_host.sh scripts/enable_full_vps_https.sh scripts/deploy_full_vps.sh \
  || fail "Deployment script syntax validation failed."

printf '%s\n' "2. Build candidate frontend, API and worker before touching live services"
compose build frontend api worker || fail "Candidate image build failed."

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

printf '%s\n' "4. Create pre-deploy PostgreSQL backup"
mkdir -p "$BACKUP_DIR"
timestamp=$(date -u +"%Y%m%dT%H%M%SZ")
short_commit=$(git rev-parse --short=12 HEAD)
backup="$BACKUP_DIR/predeploy_fullvps_${timestamp}_${short_commit}.dump"
if compose exec -T database sh -ec '
  pg_dump --format=custom --no-owner --no-privileges \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB"
' > "$backup"; then
  chmod 600 "$backup"
  echo "DATABASE_BACKUP_CREATED file=$backup"
else
  rm -f "$backup"
  fail "Database backup failed; deployment stopped before cutover."
fi

printf '%s\n' "5. Run migrations using candidate API image"
compose run --rm --no-deps api sh -ec '
  python scripts/wait_for_database.py --timeout 180
  alembic upgrade head
' || fail "Database migration failed."

printf '%s\n' "6. Recreate API, worker and frontend"
compose up -d --force-recreate api worker frontend \
  || fail "Application cutover failed."

printf '%s\n' "7. Wait for API health"
attempt=0
while [ "$attempt" -lt 60 ]; do
  attempt=$((attempt + 1))
  if curl -fsS --max-time 3 http://127.0.0.1:8080/health/database >/dev/null 2>&1; then
    break
  fi
  [ "$attempt" -lt 60 ] || fail "API database health endpoint did not become ready."
  sleep 2
done
curl -fsS --max-time 5 http://127.0.0.1:8080/health >/dev/null \
  || fail "API liveness endpoint failed."

printf '%s\n' "8. Wait for frontend health"
attempt=0
while [ "$attempt" -lt 30 ]; do
  attempt=$((attempt + 1))
  if curl -fsS --max-time 3 http://127.0.0.1:8081/healthz >/dev/null 2>&1; then
    break
  fi
  [ "$attempt" -lt 30 ] || fail "Frontend container did not become ready."
  sleep 1
done
curl -fsS --max-time 5 http://127.0.0.1:8081/ >/dev/null \
  || fail "Frontend index failed."

printf '%s\n' "9. Validate host Nginx when installed"
if command -v nginx >/dev/null 2>&1; then
  nginx -t || fail "Host Nginx configuration is invalid."
  systemctl reload nginx || fail "Host Nginx reload failed."
else
  echo "NGINX_NOT_INSTALLED run=./scripts/prepare_full_vps_host.sh"
fi

printf '%s\n' "10. Final service state"
compose ps

printf '%s\n' "============================================================"
printf '%s\n' "FULL VPS DEPLOYMENT PASSED"
printf '%s\n' "Frontend : 127.0.0.1:8081 (host Nginx publishes it)"
printf '%s\n' "API      : 127.0.0.1:8080 (host Nginx publishes /api and /oauth)"
printf '%s\n' "Realtime : host Nginx publishes /ws"
printf '%s\n' "Worker   : Docker private service"
printf '%s\n' "Database : Docker named volume preserved"
printf 'Backup   : %s\n' "$backup"
printf '%s\n' "============================================================"
