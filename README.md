# Legacy Model — Custom Strategy

Production is split into two independent planes:

- **Netlify** serves the frontend only: HTML, CSS, JavaScript, mobile UI and navigation.
- **Contabo** serves the backend only: FastAPI, PostgreSQL, the Custom Strategy worker and realtime WebSocket gateway.

Hostinger, Render, Railway and Replit are not production deployment targets for this repository.

## Production architecture

```text
Browser
  |
  | HTTPS static assets
  v
Netlify CDN
  |-- dashboard UI
  |-- /api/* proxy
  |-- /oauth/* proxy
  |
  +---------------- direct signed WSS ----------------+
  |                                                   |
  v                                                   v
Contabo HTTPS backend ------------------------> /ws/me/live
  |-- FastAPI API
  |-- PostgreSQL
  |-- Custom Strategy worker
  |-- OAuth/session state
  |-- account runtime state
  |
  +--> Deriv public market WebSocket
  +--> one authenticated private Deriv WebSocket per enabled account
```

The browser never participates in financial execution. Closing a browser tab, phone or laptop does not stop Auto Trading. Backend `ManagedAccount.enabled` state is authoritative.

## Trading execution path

The active Custom Strategy worker is `python -m app.custom_strategy_worker`.

For each enabled account:

```text
Custom Strategy condition
  -> exact account private WebSocket
  -> proposal on that same account session
  -> BUY exact proposal ID on that same account session
  -> persist contract
  -> proposal_open_contract subscription
  -> settlement
```

A temporary pre-purchase private-session interruption keeps Auto Trading enabled and reconnects the affected account. A non-financial proposal may be retried once after a transient transport failure. An uncertain BUY acknowledgement is never blindly retried because that could duplicate a financial purchase.

## Frontend — Netlify

The production frontend build is defined by `netlify.toml` and `scripts/build-netlify.mjs`.

Build settings:

```text
Production branch: main
Build command: npm run build
Publish directory: dist
```

Before the backend hostname exists, Netlify can build a static preview with no backend environment configured.

After the Contabo backend is online, configure these Netlify environment variables:

```text
BACKEND_ORIGIN=https://api.derivadmin.site
DASHBOARD_WS_BASE_URL=wss://api.derivadmin.site
```

`DASHBOARD_WS_BASE_URL` is optional when it is the WSS form of `BACKEND_ORIGIN`.

The build generates same-origin proxy rules for normal REST/OAuth traffic:

```text
/api/*   -> Contabo FastAPI
/oauth/* -> Contabo FastAPI
```

Realtime account state uses a short-lived signed ticket and a direct browser WSS connection to the Contabo backend. The ticket contains no Deriv OAuth credential and cannot buy a contract.

## Backend — Contabo

Use an Ubuntu server with Docker Engine, Docker Compose and Caddy. The production Compose stack contains only:

```text
database
api
worker
```

There is no frontend container on Contabo.

Create the production environment from:

```bash
cp .env.vps.example .env
chmod 600 .env
```

At minimum configure:

```text
POSTGRES_PASSWORD
DERIV_APP_ID
DERIV_OAUTH_CLIENT_ID
DERIV_OAUTH_REDIRECT_URL
DERIV_TOKEN_ENCRYPTION_KEY
CONTROL_API_KEY
DASHBOARD_STREAM_SIGNING_KEY
DASHBOARD_FRONTEND_ORIGINS
CORS_ALLOWED_ORIGINS
TRUSTED_HOSTS
```

The intended public host layout is:

```text
https://derivadmin.site      -> Netlify frontend
https://api.derivadmin.site  -> Contabo backend
wss://api.derivadmin.site    -> Contabo realtime gateway
```

Deploy the backend with:

```bash
sh -n scripts/deploy_dedicated_backend.sh
sh scripts/deploy_dedicated_backend.sh
```

The deployment validates source/Compose, builds API and worker images before cutover, starts PostgreSQL, creates a pre-deploy database dump, runs Alembic migrations, recreates API/worker and verifies backend health.

## OAuth

Users link Deriv accounts through OAuth 2.0 + PKCE. The frontend callback URL should be:

```text
https://derivadmin.site/oauth/callback
```

Netlify proxies `/oauth/*` to the backend. Deriv OAuth access/refresh credentials remain encrypted on the backend and are never exposed to Netlify static assets.

## Realtime dashboard

The browser obtains a short-lived ticket through `/api/me/live-ticket`, then connects directly to:

```text
wss://api.derivadmin.site/ws/me/live?ticket=...
```

Snapshots update runtime state, account balance/P&L, runs, wins, losses and recent contracts without rebuilding the whole page. A bounded HTTP snapshot path remains only as a reconnect fallback and must not block navigation.

## Local verification

```bash
python -m pip install -r requirements.txt
python -m compileall -q app scripts tests migrations
npm ci
npm run build
python -m unittest -q tests.test_netlify_vps_split
python -m unittest -q tests.test_custom_direct_runtime
python -m unittest -q tests.test_current_custom_runtime_fix
```

GitHub's Release Gate additionally checks Python lint/type safety, dashboard JavaScript syntax, focused regression suites, the production Netlify build and API/worker Docker image builds.

## Production acceptance

Before enabling real-money execution on a new Contabo server, verify one Demo account and one market end-to-end:

```text
CUSTOM_STRATEGY_SIGNAL_QUALIFIED
PURCHASE_EXECUTION_REQUEST
PURCHASE_CONFIRMED
CONTRACT_SETTLED
```

Also verify:

- Start/Stop respond without a full dashboard reload.
- Closing the browser does not change backend Auto Trading state.
- Reopening the frontend reconstructs current runtime and recent trades.
- Direct WSS reconnects after temporary disconnects.
- Dashboard delivery failures cannot delay financial settlement.
- Strategy market changes use the fast planned public-stream reconnect path.

Only after Demo acceptance should real-money switches be enabled.

## Operations

Useful Contabo backend commands:

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs -f --tail=100 worker
docker compose -f docker-compose.yml logs -f --tail=100 api
curl -i http://127.0.0.1:8080/health
sh scripts/backup_database.sh
```

For architecture details see `docs/NETLIFY_VPS_SPLIT_ARCHITECTURE.md`.
