#!/usr/bin/env sh
set -eu

compose() {
  docker compose -f docker-compose.yml -f docker-compose.vps.yml "$@"
}

compose pull
compose build

# The migration container can only resolve the Compose service name "database"
# after the PostgreSQL service and project network exist. Starting PostgreSQL
# explicitly prevents the Alembic one-off container from failing during DNS setup.
compose up -d database

attempt=0
until compose exec -T database sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "PostgreSQL did not become ready after 120 seconds." >&2
    compose logs --tail=200 database >&2
    exit 1
  fi
  sleep 2
done

# PostgreSQL is already running and healthy, so avoid asking `docker compose run`
# to recreate dependencies while the migration container joins the project network.
compose run --rm --no-deps worker alembic upgrade head

compose up -d --wait --wait-timeout 180
compose ps
