#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: scripts/restore_database.sh backups/test2_TIMESTAMP.dump" >&2
  exit 2
fi

docker compose stop worker api
docker compose exec -T database pg_restore \
  -U "${POSTGRES_USER:-underdog}" \
  -d "${POSTGRES_DB:-underdog_test2}" \
  --clean --if-exists < "$1"
docker compose up -d api worker
