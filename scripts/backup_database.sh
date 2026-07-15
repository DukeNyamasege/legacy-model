#!/usr/bin/env sh
set -eu

mkdir -p backups
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
docker compose exec -T database pg_dump \
  -U "${POSTGRES_USER:-underdog}" \
  -d "${POSTGRES_DB:-underdog_test2}" \
  -Fc > "backups/test2_${timestamp}.dump"
echo "Created backups/test2_${timestamp}.dump"
