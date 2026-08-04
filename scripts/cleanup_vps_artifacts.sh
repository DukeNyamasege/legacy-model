#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BACKUP_DIR="$PROJECT_DIR/deploy-backups"
REPORT_DIR="$PROJECT_DIR/performance-reports"
MODE=${1:-manual}
BUILD_CACHE_MAX_AGE=${BUILD_CACHE_MAX_AGE:-72h}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-14}
BACKUP_RETAIN_COUNT=${BACKUP_RETAIN_COUNT:-5}
REPORT_RETENTION_DAYS=${REPORT_RETENTION_DAYS:-7}
REPORT_RETAIN_COUNT=${REPORT_RETAIN_COUNT:-10}
ALLOW_REMOVE_RUNNING_PREFLIGHT=${ALLOW_REMOVE_RUNNING_PREFLIGHT:-false}
DEPLOYMENT_LOCK_HELD=${DEPLOYMENT_LOCK_HELD:-false}

cd "$PROJECT_DIR"

truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes) return 0 ;;
    *) return 1 ;;
  esac
}

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

prune_retained_files() {
  directory=$1
  pattern=$2
  days=$3
  retain=$4
  [ -d "$directory" ] || return 0
  keep_file=$(mktemp)
  find "$directory" -maxdepth 1 -type f -name "$pattern" -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk -v keep="$retain" 'NR <= keep {$1=""; sub(/^ /,""); print}' \
    > "$keep_file"
  find "$directory" -maxdepth 1 -type f -name "$pattern" -mtime "+$days" 2>/dev/null \
    | while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        if ! grep -Fx "$candidate" "$keep_file" >/dev/null 2>&1; then
          rm -f "$candidate"
        fi
      done
  rm -f "$keep_file"
}

echo "============================================================"
echo "SAFE VPS ARTIFACT CLEANUP"
echo "============================================================"
echo "Mode: $MODE"
echo "This cleanup preserves running production containers and every production named volume."

echo ""
echo "--- DISK BEFORE ---"
df -h / 2>/dev/null || true
docker system df 2>/dev/null || true

active_preflights=$(running_preflight_projects || true)
remove_running=false
if [ -n "$active_preflights" ]; then
  if truthy "$ALLOW_REMOVE_RUNNING_PREFLIGHT" && truthy "$DEPLOYMENT_LOCK_HELD"; then
    remove_running=true
    echo ""
    echo "Removing stale candidate project(s) under the exclusive VPS update lock:"
    printf '%s\n' "$active_preflights"
  else
    echo ""
    echo "ACTIVE CANDIDATE DEPLOYMENT DETECTED:" >&2
    printf '%s\n' "$active_preflights" >&2
    echo "Cleanup refused because the exclusive updater lock was not declared." >&2
    echo "Wait for the deployment to finish or run the normal update script." >&2
    exit 1
  fi
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

# Failed/repeated release gates use isolated Compose project names. A locked updater
# may force-remove a stale running candidate; manual cleanup removes only stopped
# remnants. Production volumes use project=legacy-model and cannot match.
docker ps -a --format '{{.ID}} {{.Status}} {{.Label "com.docker.compose.project"}}' \
  | awk '$NF ~ /^legacy-model-preflight-/ {print $1}' \
  | while IFS= read -r container_id; do
      [ -n "$container_id" ] || continue
      if [ "$remove_running" = "true" ]; then
        docker rm -f "$container_id" >/dev/null 2>&1 || true
      else
        docker rm "$container_id" >/dev/null 2>&1 || true
      fi
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

# Keep the newest backups/reports regardless of age, then expire older files. This
# prevents diagnostics and repeated predeploy dumps from becoming future disk use.
prune_retained_files "$BACKUP_DIR" 'predeploy_*.dump' "$BACKUP_RETENTION_DAYS" "$BACKUP_RETAIN_COUNT"
prune_retained_files "$REPORT_DIR" '*.log' "$REPORT_RETENTION_DAYS" "$REPORT_RETAIN_COUNT"

echo ""
echo "--- DISK AFTER ---"
df -h / 2>/dev/null || true
docker system df 2>/dev/null || true

echo "Cleanup complete. Production containers, test2_database and test2_models were preserved."
