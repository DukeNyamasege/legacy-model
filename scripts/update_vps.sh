#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_DIR"

fail() {
  echo "VPS UPDATE ABORTED: $1" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || fail "git is not installed"
[ -f .env ] || fail "Missing .env. Copy .env.vps.example to .env and configure it first."

git diff --quiet || fail "Tracked files have local changes. Commit or restore them before deployment."
git diff --cached --quiet || fail "The Git index contains staged changes. Commit or unstage them first."

PREVIOUS_COMMIT=$(git rev-parse HEAD)
echo "Current VPS commit: $PREVIOUS_COMMIT"

git fetch origin
git checkout main
git pull --ff-only origin main

CURRENT_COMMIT=$(git rev-parse HEAD)
echo "Target VPS commit : $CURRENT_COMMIT"

chmod +x scripts/deploy_vps.sh
DEPLOY_PREVIOUS_COMMIT="$PREVIOUS_COMMIT" ./scripts/deploy_vps.sh
