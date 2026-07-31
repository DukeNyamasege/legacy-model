#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
STATE_DIR="$PROJECT_DIR/.deployment_state"
LAST_SUCCESSFUL_COMMIT_FILE="$STATE_DIR/last_successful_commit"
PENDING_FROM_COMMIT_FILE="$STATE_DIR/pending_from_commit"
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
[ -f .env ] || fail "Missing .env. Copy .env.vps.example to .env and configure it first."

git diff --quiet || fail "Tracked files have local changes. Commit or restore them before deployment."
git diff --cached --quiet || fail "The Git index contains staged changes. Commit or unstage them first."

git checkout main
mkdir -p "$STATE_DIR"
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

chmod +x scripts/deploy_vps.sh scripts/update_vps.sh

if DEPLOY_PREVIOUS_COMMIT="$PREVIOUS_COMMIT" ./scripts/deploy_vps.sh; then
  exit 0
else
  status=$?
  echo "Deployment failed. The comparison base was retained in $PENDING_FROM_COMMIT_FILE" >&2
  exit "$status"
fi
