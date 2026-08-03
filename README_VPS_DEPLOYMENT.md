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
`DERIV_ENVIRONMENT=demo`, and `ALLOW_REAL_TRADING=false`. The stack keeps the
API published only on the host loopback interface, and Caddy on the host reverse
proxies traffic to it so the public site is served at
`https://derivadmin.site`.

DNS should publish an `A` record for `derivadmin.site` and a `CNAME` for
`www.derivadmin.site` pointing to `derivadmin.site`. Do not publish an `AAAA`
record unless the VPS firewall and Caddy are verified over IPv6; otherwise some
mobile/IPv6-preferred networks can time out while IPv4 users load normally.

Required variables are `DERIV_APP_ID`, `DERIV_TOKEN`, `DERIV_ENVIRONMENT`,
`DERIV_TRADING_ENABLED`, `TRADING_MODE`, `ALLOW_REAL_TRADING`, `TEST_RUN_ID`,
`CONTROL_API_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`PORT`.

## Caddy

Point `derivadmin.site` at your VPS IP, then use a Caddyfile like this:

```caddy
derivadmin.site, www.derivadmin.site {
    encode zstd gzip

    @www host www.derivadmin.site
    redir @www https://derivadmin.site{uri} permanent

    reverse_proxy 127.0.0.1:8080

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
    }
}
```

After copying it to `/etc/caddy/Caddyfile`, run:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

## Health Check

Once the stack is up and DNS is pointed at the VPS, verify:

```bash
curl -i https://derivadmin.site/health
curl -I https://derivadmin.site/
curl -I https://derivadmin.site/ui/dashboard-v2.css
curl -I https://www.derivadmin.site/health
```

Then open `https://derivadmin.site`.

## Safe Operations

```bash
docker compose logs -f worker
docker compose restart api
./scripts/backup_database.sh
./scripts/restore_database.sh backups/test2_TIMESTAMP.dump
```

### Reset trade history without deleting traders

Stop the worker before resetting so no purchase or settlement can race the
database transaction. Always create a backup first. The reset command clears
all historical personal trades, canonical model trades, virtual/shadow runs,
signals, ticks, streaks, and active recovery state while preserving managed
trader accounts, encrypted credentials, browser sessions, balances, controls,
OAuth state, and model artifacts.

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml stop worker
./scripts/backup_database.sh
docker compose -f docker-compose.yml -f docker-compose.vps.yml run --rm worker \
  python scripts/reset_test_data.py --target test2 --confirm RESET_TEST2 --all-runs
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d worker
```

The reset refuses to proceed while a potentially active purchased contract is
recorded. Do not use `--allow-expired-one-tick` unless Deriv has independently
confirmed that every listed contract has expired and the worker cannot
reconcile it.

Pause or resume through the authenticated API before maintenance. During an
upgrade, leave the old worker running until the new image is built, pause it,
then run `./scripts/deploy_vps.sh`. The database lease prevents overlap if two
worker containers briefly coexist, but Compose should still keep one worker
replica.

PostgreSQL data and HMM metadata use named volumes. `SIGTERM` and `SIGINT` are
handled by the worker so it can stop new entries, finish cleanup, and release its
lease. If a host dies abruptly, the lease expires and the replacement worker can
recover unresolved contracts from PostgreSQL.
