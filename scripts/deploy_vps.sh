#!/usr/bin/env sh
set -eu

docker compose -f docker-compose.yml -f docker-compose.vps.yml pull
docker compose -f docker-compose.yml -f docker-compose.vps.yml build
# Run migrations from the worker image so the live API can keep serving traffic
# during the migration step. One-off containers intentionally do not require a
# fixed IP or published dashboard port.
docker compose -f docker-compose.yml -f docker-compose.vps.yml run --rm worker alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d --wait --wait-timeout 120
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
