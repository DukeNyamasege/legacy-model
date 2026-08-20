# Legacy Model — Custom Strategy

`derivadmin.site` is a **single-VPS production application**.

## Production architecture

```text
Internet
  |
  v
Caddy :443
  |-- /              -> frontend container 127.0.0.1:8081
  |-- /api/*         -> FastAPI container   127.0.0.1:8080
  |-- /oauth/*       -> FastAPI container   127.0.0.1:8080
  `-- /ws/*          -> FastAPI WebSocket   127.0.0.1:8080

Docker Compose
  |-- frontend
  |-- api
  |-- worker
  `-- database (PostgreSQL named volume)
```

Frontend, API, OAuth, realtime, worker execution and PostgreSQL all run on the VPS. Caddy is the public HTTPS boundary.

## Production entrypoints

- API: `app.vps_backend_api:app`
- Core API bootstrap: `app.vps_core_api`
- Realtime gateway: `app.vps_realtime_gateway`
- Worker: `python -m app.custom_strategy_worker`
- Frontend build: `scripts/build-vps.mjs`
- Full deployment: `scripts/deploy_full_vps.sh`
- Edge configuration: `Caddyfile`

## Trading lifecycle invariant

Once an account has been started, execution remains enabled until one of these terminal events occurs:

1. Take Profit is reached.
2. Stop Loss is reached.
3. The user explicitly presses Stop.

Transport failures, provider timeouts, OTP reconnects, temporary insufficient balance responses, proposal errors, contract reconciliation and internal runtime errors are recovery states, not automatic Stop events.

The durable direct hard-stop sentinel protects explicit user Stop at the final pre-BUY boundary. TP and SL remain deliberate financial terminal states.

## Execution path

For an enabled account:

```text
Custom Strategy condition
  -> exact account authenticated Deriv WebSocket
  -> proposal
  -> BUY exact proposal ID
  -> persist contract
  -> proposal_open_contract monitoring
  -> settlement
  -> account/recovery state update
```

An uncertain BUY acknowledgement is reconciled before any later purchase and is never blindly retried.

## OAuth

Users link Deriv accounts with OAuth 2.0 + PKCE. The production callback is:

```text
https://derivadmin.site/oauth/callback
```

OAuth credentials remain server-side. The browser receives server session state and short-lived realtime tickets, not reusable Deriv trading credentials.

## Realtime dashboard

The authenticated browser obtains a short-lived ticket through:

```text
/api/me/live-ticket
```

and connects to:

```text
wss://derivadmin.site/ws/me/live?ticket=...
```

The VPS realtime gateway streams account-scoped snapshots and heartbeats. Browser disconnects do not control worker lifetime or stop Auto Trading.

## VPS deployment

```bash
cd /root/legacy-model
git fetch origin --prune
git checkout main
git reset --hard origin/main
PUBLIC_ORIGIN=https://derivadmin.site ./scripts/deploy_full_vps.sh
```

The deployment builds candidate frontend/API/worker images first, verifies PostgreSQL, creates a pre-cutover database dump, runs Alembic migrations, recreates services and validates Caddy-facing health.

Never use:

```bash
docker compose down -v
```

because `-v` removes named volumes.

## Production checks

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS https://derivadmin.site/backend-health

docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
docker compose -f docker-compose.yml -f docker-compose.vps.yml logs --tail=200 api worker frontend
```

For the full host procedure see `docs/FULL_VPS_DEPLOYMENT.md`.
