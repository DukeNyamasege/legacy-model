#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_DIR"

echo "============================================================"
echo "SAFE REPOSITORY CLEANUP"
echo "============================================================"

current_branch=$(git branch --show-current 2>/dev/null || true)
if [ "$current_branch" != "main" ]; then
  echo "Refusing cleanup: checkout main first (current=$current_branch)." >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Refusing cleanup: working tree has uncommitted changes." >&2
  git status --short >&2
  exit 1
fi

echo "Fetching and pruning deleted remote references..."
git fetch origin --prune

echo "Removing only local branches already merged into main..."
git branch --merged main \
  | sed 's/^[* ]*//' \
  | grep -Ev '^(main|master)$' \
  | while IFS= read -r branch; do
      [ -n "$branch" ] || continue
      git branch -d "$branch" >/dev/null 2>&1 || true
    done

echo "Removing ignored build/cache files only..."
# Preserve runtime secrets and retained operational data even when they are ignored.
git clean -fdX \
  -e .env \
  -e '.env.*' \
  -e deploy-backups/ \
  -e performance-reports/

echo "Repacking Git objects and pruning unreachable local objects..."
git reflog expire --expire=30.days.ago --all || true
git gc --prune=30.days.ago

echo "Running Docker/deployment artifact cleanup..."
sh scripts/cleanup_vps_artifacts.sh repository-cleanup

echo ""
echo "Repository cleanup complete. Current main worktree, runtime secrets, retained backups and running production containers were preserved."
git status --short
git branch --show-current
git rev-parse HEAD
