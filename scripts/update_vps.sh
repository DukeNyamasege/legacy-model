#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
STATE_DIR="$PROJECT_DIR/.deployment_state"
REPORT_DIR="$PROJECT_DIR/performance-reports"
LAST_SUCCESSFUL_COMMIT_FILE="$STATE_DIR/last_successful_commit"
PENDING_FROM_COMMIT_FILE="$STATE_DIR/pending_from_commit"
DEPLOY_LOCK_FILE="$STATE_DIR/vps-update.lock"
cd "$PROJECT_DIR"

fail() {
  echo "VPS UPDATE ABORTED: $1" >&2
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

command -v git >/dev/null 2>&1 || fail "git is not installed"
command -v flock >/dev/null 2>&1 || fail "flock is not installed"
[ -f .env ] || fail "Missing .env. Copy .env.vps.example to .env and configure it first."
mkdir -p "$STATE_DIR" "$REPORT_DIR"

# Hold one kernel-managed lock for the entire diagnostics/cleanup/build/test/cutover
# cycle. A killed shell releases it automatically, so no stale lock file blocks
# recovery and two updates cannot delete or replace each other's candidate stack.
exec 9>"$DEPLOY_LOCK_FILE"
flock -n 9 || fail "Another VPS update is already running"

# VPS deployments may change executable bits with chmod. Git content safety must
# still reject real edits, while permission-only changes must not block updates.
git -c core.fileMode=false diff --quiet \
  || fail "Tracked file contents have local changes. Commit, back up, or restore them before deployment."
git -c core.fileMode=false diff --cached --quiet \
  || fail "The Git index contains staged content changes. Commit or unstage them first."

git checkout main
CURRENT_CHECKOUT=$(git rev-parse HEAD)
PREVIOUS_COMMIT=${DEPLOY_PREVIOUS_COMMIT:-}

# A failed deployment can leave the Git checkout newer than the code that last
# passed deployment. Prefer the confirmed successful marker. The pending marker
# is only a fallback for an attempt made before any successful marker existed.
if ! valid_commit "$PREVIOUS_COMMIT" && [ -f "$LAST_SUCCESSFUL_COMMIT_FILE" ]; then
  PREVIOUS_COMMIT=$(sed -n '1p' "$LAST_SUCCESSFUL_COMMIT_FILE" | tr -d '[:space:]')
fi
if ! valid_commit "$PREVIOUS_COMMIT" && [ -f "$PENDING_FROM_COMMIT_FILE" ]; then
  PREVIOUS_COMMIT=$(sed -n '1p' "$PENDING_FROM_COMMIT_FILE" | tr -d '[:space:]')
fi
if ! valid_commit "$PREVIOUS_COMMIT"; then
  PREVIOUS_COMMIT=$CURRENT_CHECKOUT
fi
write_state_file "$PENDING_FROM_COMMIT_FILE" "$PREVIOUS_COMMIT"

echo "Last successfully deployed comparison base: $PREVIOUS_COMMIT"
echo "Current Git checkout                    : $CURRENT_CHECKOUT"

git fetch origin
git pull --ff-only origin main

CURRENT_COMMIT=$(git rev-parse HEAD)
echo "Target VPS commit                       : $CURRENT_COMMIT"

# Invoke through sh instead of chmod so the updater never modifies Git executable
# bits. Validate every operational script before deleting or creating artifacts.
sh -n \
  scripts/deploy_vps.sh \
  scripts/update_vps.sh \
  scripts/run_legacy_release_tests.sh \
  scripts/cleanup_vps_artifacts.sh \
  scripts/diagnose_vps_performance.sh

# Capture the slow system exactly as it exists before old candidate containers,
# images or build cache are removed. This read-only report lets us distinguish
# disk capacity from CPU, memory, block I/O, API latency and PostgreSQL pressure.
PREDEPLOY_REPORT="$REPORT_DIR/pre-cleanup-$(date -u +"%Y%m%dT%H%M%SZ").log"
echo "Collecting pre-cleanup performance evidence: $PREDEPLOY_REPORT"
PERFORMANCE_REPORT_FILE="$PREDEPLOY_REPORT" \
  sh scripts/diagnose_vps_performance.sh || true

# The exclusive lock proves no second updater is using an isolated candidate.
# Therefore a running preflight project at this point is residue from an interrupted
# older deployment and may be removed. Production containers/volumes never match.
DEPLOYMENT_LOCK_HELD=true \
  ALLOW_REMOVE_RUNNING_PREFLIGHT=true \
  sh scripts/cleanup_vps_artifacts.sh pre-deploy

if DEPLOY_PREVIOUS_COMMIT="$PREVIOUS_COMMIT" sh ./scripts/deploy_vps.sh; then
  # The successful cutover can leave the former API/worker image dangling. Prune
  # it after the new containers are confirmed, again preserving active images.
  DEPLOYMENT_LOCK_HELD=true \
    ALLOW_REMOVE_RUNNING_PREFLIGHT=true \
    sh scripts/cleanup_vps_artifacts.sh post-deploy || true
  exit 0
else
  status=$?
  echo "Deployment failed. The comparison base was retained in $PENDING_FROM_COMMIT_FILE" >&2
  echo "Collecting a token-free performance report for the failed attempt..." >&2
  sh scripts/diagnose_vps_performance.sh || true
  exit "$status"
fi
