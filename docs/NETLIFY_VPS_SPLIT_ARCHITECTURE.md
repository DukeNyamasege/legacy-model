# Netlify Frontend + Contabo Backend

## Production boundary

The production application is intentionally split into two independent planes.

```text
Browser
  |
  | HTTPS static assets
  v
Netlify CDN
  |-- index.html / CSS / JavaScript
  |
  | same-origin /api/* and /oauth/* proxy
  v
Contabo HTTPS API (Caddy -> FastAPI)
  |-- OAuth/session management
  |-- account settings / Start / Stop
  |-- Custom Strategy persistence
  |-- signed short-lived realtime tickets
  |-- direct /ws/me/live WebSocket
  |
  +-------------------- PostgreSQL

Browser ------------------ direct WSS -----------------> Contabo FastAPI
               signed short-lived ticket

Contabo worker
  |-- one public Deriv market WebSocket
  |-- one authenticated private WebSocket per enabled account
  |-- Custom Strategy evaluation in memory
  |-- proposal + BUY on the exact account session
  |-- proposal_open_contract settlement subscription
  +-- durable trade/runtime updates -> PostgreSQL

Worker -- best-effort nonblocking wake-up --> FastAPI realtime hub
```

## Ownership rules

### Netlify owns only the frontend

Netlify serves static HTML, CSS and JavaScript. It does not execute a strategy, open a Deriv account session, place a contract, settle a trade or decide whether Auto Trading is running.

The browser sends normal REST/OAuth traffic to same-origin `/api/*` and `/oauth/*` URLs. The generated Netlify `_redirects` file proxies those requests to the Contabo backend. This keeps normal browser actions on one frontend origin.

### Contabo owns all trading state

FastAPI, the Custom Strategy worker and PostgreSQL run on Contabo. `ManagedAccount.enabled` and backend execution status are authoritative. Closing a phone/browser tab has no effect on the worker. Auto Trading continues until an explicit backend stop/risk condition changes the account state.

The worker keeps the direct account-scoped execution architecture:

```text
Custom Strategy condition
  -> exact account private WebSocket
  -> proposal
  -> exact proposal ID BUY on the same private WebSocket
  -> persist contract
  -> proposal_open_contract subscription
  -> settlement
```

No browser connection participates in the financial execution chain.

## Realtime dashboard design

The production frontend does not depend on a fixed 15-second dashboard polling cycle and does not route a long-lived SSE stream through Netlify.

1. The browser requests `/api/me/live-ticket` through the same-origin Netlify REST proxy.
2. FastAPI verifies the current server session and returns a short-lived signed ticket.
3. The browser opens `wss://api.derivadmin.site/ws/me/live?ticket=...` directly to Contabo.
4. FastAPI verifies the ticket, still-valid server session, managed account ID and browser Origin.
5. Runtime/trade changes produce combined account snapshots that update the existing DOM directly without rebuilding the whole dashboard.
6. If WSS reconnects, bounded HTTP snapshot fallback keeps the last rendered dashboard usable. Navigation is never blocked by that fallback.

The WebSocket ticket contains no Deriv OAuth token and cannot be used to purchase a contract. Deriv credentials stay encrypted on the backend.

## Start and Stop

Start and Stop remain short backend commands. The browser does not wait for the worker to finish connecting before regaining control of the UI.

- Start writes `enabled=true` and a starting runtime state.
- The worker independently establishes/reuses the account private WebSocket.
- Realtime state moves to Waiting/Executing/Running as the worker progresses.
- Stop writes the backend stop state.
- Closing a tab does not call Stop.

## Trading transport resilience

### Proposal timeout

A proposal request is non-financial. The worker bridge permits one short retry for a temporary proposal/session transport interruption.

### BUY acknowledgement timeout

A BUY is financial. It is never blindly retried after an acknowledgement timeout because Deriv may have accepted the purchase even if the response was lost. That account fails closed for reconciliation instead of risking a duplicate BUY.

### Private account session interruption

A temporary pre-purchase private-session interruption does not permanently disable Auto Trading. The account leaves the current hot execution set, remains enabled, reconnects independently and becomes eligible again only after its authenticated session is ready.

### Public market stream

Real provider/network failures retain bounded exponential/rate-limit backoff. Intentional `custom_market_set_changed` restarts use a separate fast reconnect path so adding/removing strategy markets does not inherit outage backoff.

## Dashboard work cannot delay settlement

Worker-to-API dashboard wake-ups are best-effort background events. Contract persistence and settlement do not await frontend notification. If that internal wake-up is unavailable, the realtime WebSocket checks durable database revisions and recovers automatically.

The former heavy global summary path is not part of the Netlify Custom Strategy production frontend.

## Deployment configuration

### Netlify

After the Contabo backend has a stable HTTPS hostname, set:

```text
BACKEND_ORIGIN=https://api.derivadmin.site
```

Optional explicit override:

```text
DASHBOARD_WS_BASE_URL=wss://api.derivadmin.site
```

`npm run build` produces `dist/`, injects the frontend/realtime scripts, appends the final compact mobile CSS and writes `_redirects` for `/api/*` and `/oauth/*`.

### Contabo

Use a clean Ubuntu deployment with Docker Engine, Docker Compose and Caddy. Create `/root/legacy-model/.env` from `.env.vps.example` and configure at minimum:

- PostgreSQL password
- Deriv app/OAuth settings
- token encryption key
- control API key
- realtime signing key
- Netlify/custom frontend origins
- backend trusted hostname
- Demo/Real execution switches

The Contabo Compose stack intentionally contains only:

```text
database
api
worker
```

There is no production frontend container.

The supported backend deployment command is:

```bash
sh scripts/deploy_dedicated_backend.sh
```

The repository no longer maintains Hostinger-, Railway-, Render- or Replit-specific production deployment paths.

## Host layout

```text
https://derivadmin.site      -> Netlify
https://api.derivadmin.site  -> Contabo / Caddy / FastAPI
wss://api.derivadmin.site    -> Contabo realtime gateway
```

The Deriv OAuth callback remains on the frontend origin:

```text
https://derivadmin.site/oauth/callback
```

Netlify proxies the callback to Contabo.

## Acceptance gates before real-money use

1. Netlify static build completes and loads without Contabo serving UI assets.
2. OAuth returns to the Netlify frontend and creates a working server session.
3. `/api/me` and Start/Stop stay bounded and responsive.
4. Direct WSS reaches Connected and survives temporary disconnect/reconnect.
5. One Demo account produces `CUSTOM_STRATEGY_SIGNAL_QUALIFIED -> PURCHASE_EXECUTION_REQUEST -> PURCHASE_CONFIRMED -> CONTRACT_SETTLED`.
6. Closing the browser for several trades does not change `ManagedAccount.enabled`.
7. Reopening the browser reconstructs current runtime/trade state from PostgreSQL.
8. A deliberate public market-set restart reconnects quickly without outage backoff.
9. A dashboard wake-up failure cannot delay contract settlement.
10. Only after these Demo gates pass should Real execution be enabled on Contabo.
