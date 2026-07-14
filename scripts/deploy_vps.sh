#!/usr/bin/env sh
set -eu

docker compose -f docker-compose.yml -f docker-compose.vps.yml pull
docker compose -f docker-compose.yml -f docker-compose.vps.yml build
# The VPS API owns a fixed Docker IP for Caddy, so a one-off API container would
# collide with the live API. The worker image has the same migrations and uses a
# dynamic address, making it safe to run alongside the current deployment.
docker compose -f docker-compose.yml -f docker-compose.vps.yml run --rm worker alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
