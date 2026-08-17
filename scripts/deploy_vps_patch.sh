#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.vps.yml)
CADDY_RUNTIME_FILE="/etc/caddy/Caddyfile"
GREEN_NAME="legacy-model-api-green"
GREEN_PORT="${VPS_GREEN_API_PORT:-8082}"

log() { printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

wait_http() {
  local url="$1"
  local attempts="${2:-45}"
  local delay="${3:-1}"
  for ((i=1; i<=attempts; i++)); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done
  return 1
}

backup_database() {
  mkdir -p deploy-backups
  local stamp backup
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="deploy-backups/pre_targeted_api_${stamp}_$(git rev-parse --short=12 HEAD).dump"
  "${COMPOSE[@]}" exec -T database sh -lc \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
    > "$backup"
  test -s "$backup"
  echo "DATABASE_BACKUP_CREATED=$backup"
}

deploy_frontend_hot() {
  log "Building frontend candidate only; API and worker remain untouched"
  "${COMPOSE[@]}" build frontend

  local live candidate tmp
  live="$("${COMPOSE[@]}" ps -q frontend)"
  if [[ -z "$live" ]] || ! docker inspect -f '{{.State.Running}}' "$live" 2>/dev/null | grep -q true; then
    log "Frontend is not running; starting the built image normally"
    "${COMPOSE[@]}" up -d --no-deps frontend
    wait_http http://127.0.0.1:8081/healthz 30 1
    return
  fi

  candidate="legacy-model-frontend-patch-$$"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp" >/dev/null 2>&1 || true; docker rm -f "$candidate" >/dev/null 2>&1 || true' RETURN

  docker create --name "$candidate" legacy-model-frontend:latest >/dev/null
  mkdir -p "$tmp/html"
  docker cp "$candidate:/usr/share/nginx/html/." "$tmp/html/"
  docker rm "$candidate" >/dev/null

  log "Atomically replacing static frontend files inside the running Nginx container"
  docker exec "$live" sh -ec 'rm -rf /usr/share/nginx/html.next /usr/share/nginx/html.previous; mkdir -p /usr/share/nginx/html.next'
  docker cp "$tmp/html/." "$live:/usr/share/nginx/html.next/"
  docker exec "$live" sh -ec '
    mv /usr/share/nginx/html /usr/share/nginx/html.previous
    mv /usr/share/nginx/html.next /usr/share/nginx/html
  '

  if ! wait_http http://127.0.0.1:8081/healthz 10 1; then
    log "New static frontend failed health check; rolling files back without restarting Nginx"
    docker exec "$live" sh -ec '
      rm -rf /usr/share/nginx/html.failed
      mv /usr/share/nginx/html /usr/share/nginx/html.failed
      mv /usr/share/nginx/html.previous /usr/share/nginx/html
    '
    exit 1
  fi
  docker exec "$live" sh -ec 'rm -rf /usr/share/nginx/html.previous'
  log "Frontend hot patch active; no container restart occurred"
}

deploy_api_blue_green() {
  log "Backing up PostgreSQL before targeted API patch"
  backup_database

  log "Building API candidate only; worker keeps trading on its existing process"
  "${COMPOSE[@]}" build api
  docker rm -f "$GREEN_NAME" >/dev/null 2>&1 || true

  log "Starting green API candidate on loopback port $GREEN_PORT"
  "${COMPOSE[@]}" run -d --name "$GREEN_NAME" --no-deps \
    -p "127.0.0.1:${GREEN_PORT}:8080" api >/dev/null

  if ! wait_http "http://127.0.0.1:${GREEN_PORT}/health/live" 60 1; then
    docker logs --tail=200 "$GREEN_NAME" || true
    docker rm -f "$GREEN_NAME" >/dev/null 2>&1 || true
    echo "GREEN_API_FAILED_HEALTH_CHECK" >&2
    exit 1
  fi

  if [[ ! -f "$CADDY_RUNTIME_FILE" ]]; then
    echo "Caddy runtime file not found: $CADDY_RUNTIME_FILE" >&2
    docker rm -f "$GREEN_NAME" >/dev/null 2>&1 || true
    exit 1
  fi

  local backup temp switched
  backup="$(mktemp)"
  temp="$(mktemp)"
  switched=0
  cp "$CADDY_RUNTIME_FILE" "$backup"

  cleanup_api_patch() {
    if [[ "$switched" == "1" ]]; then
      if wait_http http://127.0.0.1:8080/health/live 2 1; then
        cp "$backup" "$CADDY_RUNTIME_FILE"
        caddy validate --config "$CADDY_RUNTIME_FILE" >/dev/null 2>&1 || true
        systemctl reload caddy >/dev/null 2>&1 || true
        switched=0
      else
        echo "PRIMARY_API_NOT_HEALTHY_GREEN_LEFT_SERVING=true" >&2
        return
      fi
    fi
    docker rm -f "$GREEN_NAME" >/dev/null 2>&1 || true
    rm -f "$backup" "$temp" >/dev/null 2>&1 || true
  }
  trap cleanup_api_patch RETURN

  sed "s#127\.0\.0\.1:8080#127.0.0.1:${GREEN_PORT}#g" "$backup" > "$temp"
  caddy validate --config "$temp" >/dev/null
  cp "$temp" "$CADDY_RUNTIME_FILE"
  systemctl reload caddy
  switched=1

  log "Public API traffic is on the healthy green candidate; recreating primary API in the background"
  "${COMPOSE[@]}" up -d --no-deps --force-recreate api

  if ! wait_http http://127.0.0.1:8080/health/live 60 1; then
    "${COMPOSE[@]}" logs --tail=250 --no-color api || true
    echo "PRIMARY_API_FAILED_HEALTH_CHECK_GREEN_STILL_SERVING=true" >&2
    exit 1
  fi

  log "Switching Caddy back to the healthy primary API"
  cp "$backup" "$CADDY_RUNTIME_FILE"
  caddy validate --config "$CADDY_RUNTIME_FILE" >/dev/null
  systemctl reload caddy
  switched=0
  docker rm -f "$GREEN_NAME" >/dev/null 2>&1 || true
  rm -f "$backup" "$temp" >/dev/null 2>&1 || true
  trap - RETURN

  log "API blue-green patch complete; worker was never restarted"
}

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy_vps_patch.sh frontend
  scripts/deploy_vps_patch.sh api
  scripts/deploy_vps_patch.sh api frontend

The script never restarts the worker. Frontend files are hot-swapped inside the
running Nginx container. API changes use a temporary green API and a graceful Caddy
switch so the primary API can be recreated while public traffic remains served.
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

for target in "$@"; do
  case "$target" in
    frontend) deploy_frontend_hot ;;
    api) deploy_api_blue_green ;;
    worker)
      echo "Worker hot deployment is intentionally blocked: duplicate execution is unsafe." >&2
      echo "Use the controlled worker restart procedure only when worker code actually changes." >&2
      exit 3
      ;;
    *) usage; exit 2 ;;
  esac
done

log "TARGETED_VPS_PATCH_COMPLETE targets=$* commit=$(git rev-parse HEAD) worker_restarted=false"
