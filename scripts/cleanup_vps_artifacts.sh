#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BACKUP_DIR="$PROJECT_DIR/deploy-backups"
MODE=${1:-manual}
BUILD_CACHE_MAX_AGE=${BUILD_CACHE_MAX_AGE:-72h}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-14}
BACKUP_RETAIN_COUNT=${BACKUP_RETAIN_COUNT:-5}

cd "$PROJECT_DIR"

is_active_image_id() {
  candidate=$1
  docker ps -q | while IFS= read -r container_id; do
    [ -n "$container_id" ] || continue
    docker inspect -f '{{.Image}}' "$container_id" 2>/dev/null || true
  done | grep -Fx "$candidate" >/dev/null 2>&1
}

running_preflight_projects() {
  docker ps --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null \
    | awk '/^legacy-model-preflight-/ {print}' \
    | sort -u
}

echo "============================================================"
echo "SAFE VPS ARTIFACT CLEANUP"
echo "============================================================"
echo "Mode: $MODE"
echo "This cleanup preserves running containers and every production named volume."

echo ""
echo "--- DISK BEFORE ---"
df -h / 2>/dev/null || true
docker system df 2>/dev/null || true

active_preflights=$(running_preflight_projects || true)
if [ -n "$active_preflights" ]; then
  echo ""
  echo "ACTIVE CANDIDATE DEPLOYMENT DETECTED:" >&2
  printf '%s\n' "$active_preflights" >&2
  echo "Cleanup refused to avoid deleting a release gate that may still be running." >&2
  echo "Wait for that deployment to finish, or inspect it manually before retrying." >&2
  exit 1
fi

# Remove exited one-shot containers belonging to this production Compose project.
# Running api/worker/database containers are never selected.
docker ps -aq \
  --filter 'label=com.docker.compose.project=legacy-model' \
  --filter 'status=exited' \
  | while IFS= read -r container_id; do
      [ -n "$container_id" ] || continue
      docker rm "$container_id" >/dev/null 2>&1 || true
    done

# Failed/repeated release gates use isolated Compose project names. No active
# candidate exists at this point, so only stopped/created remnants are selected.
# Production volumes use project=legacy-model and cannot match this section.
docker ps -a --format '{{.ID}} {{.Status}} {{.Label "com.docker.compose.project"}}' \
  | awk '$NF ~ /^legacy-model-preflight-/ {print $1}' \
  | while IFS= read -r container_id; do
      [ -n "$container_id" ] && docker rm "$container_id" >/dev/null 2>&1 || true
    done

docker network ls --format '{{.ID}} {{.Label "com.docker.compose.project"}}' \
  | awk '$2 ~ /^legacy-model-preflight-/ {print $1}' \
  | while IFS= read -r network_id; do
      [ -n "$network_id" ] && docker network rm "$network_id" >/dev/null 2>&1 || true
    done

docker volume ls --format '{{.Name}} {{.Label "com.docker.compose.project"}}' \
  | awk '$2 ~ /^legacy-model-preflight-/ {print $1}' \
  | while IFS= read -r volume_name; do
      [ -n "$volume_name" ] && docker volume rm "$volume_name" >/dev/null 2>&1 || true
    done

# Delete preflight images only when no running container uses the underlying image
# ID. Old production layers that became dangling are removed by image prune; the
# active API and worker images remain protected by Docker references.
docker images --no-trunc --format '{{.Repository}} {{.ID}}' \
  | awk '$1 ~ /^legacy-model-preflight-/ {print $2}' \
  | sort -u \
  | while IFS= read -r image_id; do
      [ -n "$image_id" ] || continue
      if ! is_active_image_id "$image_id"; then
        docker image rm "$image_id" >/dev/null 2>&1 || true
      fi
    done

docker image prune -f >/dev/null 2>&1 || true

# BuildKit cache is often the largest residue after repeated candidate builds.
# A bounded age filter avoids throwing away layers from the deployment currently
# being prepared while removing older unused build cache.
docker builder prune -f --filter "until=$BUILD_CACHE_MAX_AGE" >/dev/null 2>&1 || true

# Retain recent database backups. Never remove the newest configured count even if
# they are older than the day limit.
if [ -d "$BACKUP_DIR" ]; then
  keep_file=$(mktemp)
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'predeploy_*.dump' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk -v keep="$BACKUP_RETAIN_COUNT" 'NR <= keep {$1=""; sub(/^ /,""); print}' \
    > "$keep_file"
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'predeploy_*.dump' -mtime "+$BACKUP_RETENTION_DAYS" 2>/dev/null \
    | while IFS= read -r backup; do
        [ -n "$backup" ] || continue
        if ! grep -Fx "$backup" "$keep_file" >/dev/null 2>&1; then
          rm -f "$backup"
        fi
      done
  rm -f "$keep_file"
fi

echo ""
echo "--- DISK AFTER ---"
df -h / 2>/dev/null || true
docker system df 2>/dev/null || true

echo "Cleanup complete. Production containers, test2_database and test2_models were preserved."
