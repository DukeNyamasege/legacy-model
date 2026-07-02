#!/usr/bin/env sh
set -eu

docker compose -f docker-compose.yml -f docker-compose.vps.yml pull
docker compose -f docker-compose.yml -f docker-compose.vps.yml build
docker compose -f docker-compose.yml -f docker-compose.vps.yml run --rm api alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
