# Test 2 Ubuntu VPS Deployment

## Prerequisites

Use an Ubuntu host with Docker Engine and the Docker Compose plugin installed.
Clone the repository into a non-public directory and keep the repository private.

## Configure And Start

```bash
cp .env.vps.example .env
nano .env
chmod +x scripts/*.sh
./scripts/deploy_vps.sh
docker compose logs -f worker
```

Set every placeholder in `.env`. Keep `TRADING_MODE=demo`,
`DERIV_ENVIRONMENT=demo`, and `ALLOW_REAL_TRADING=false`. The stack exposes the
API only on `127.0.0.1` through `docker-compose.vps.yml`; place an HTTPS reverse
proxy such as Caddy or Nginx in front of it.

Required variables are `DERIV_APP_ID`, `DERIV_TOKEN`, `DERIV_ENVIRONMENT`,
`DERIV_TRADING_ENABLED`, `TRADING_MODE`, `ALLOW_REAL_TRADING`, `TEST_RUN_ID`,
`CONTROL_API_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`PORT`.

## Safe Operations

```bash
docker compose logs -f worker
docker compose restart api
./scripts/backup_database.sh
./scripts/restore_database.sh backups/test2_TIMESTAMP.dump
```

Pause or resume through the authenticated API before maintenance. During an
upgrade, leave the old worker running until the new image is built, pause it,
then run `./scripts/deploy_vps.sh`. The database lease prevents overlap if two
worker containers briefly coexist, but Compose should still keep one worker
replica.

PostgreSQL data and HMM metadata use named volumes. `SIGTERM` and `SIGINT` are
handled by the worker so it can stop new entries, finish cleanup, and release its
lease. If a host dies abruptly, the lease expires and the replacement worker can
recover unresolved contracts from PostgreSQL.
