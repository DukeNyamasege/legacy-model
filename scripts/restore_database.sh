#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: scripts/restore_database.sh backups/test2_TIMESTAMP.dump" >&2
  exit 2
fi

docker compose stop worker api
docker compose exec -T database sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  < "$1"
docker compose up -d api worker
