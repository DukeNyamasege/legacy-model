# Netlify Frontend + Dedicated VPS Backend

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
Dedicated VPS HTTPS API (Caddy -> FastAPI)
  |-- OAuth/session management
  |-- account settings / Start / Stop
  |-- Custom Strategy persistence
  |-- signed 45-second realtime tickets
  |-- direct /ws/me/live WebSocket
  |
  +-------------------- PostgreSQL

Browser ------------------ direct WSS -----------------> VPS FastAPI
               signed short-lived ticket

Dedicated VPS worker
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

Netlify serves static HTML, CSS and JavaScript. It does not execute a strategy,
open a Deriv account session, place a contract, settle a trade, or decide whether
Auto Trading is running.

The browser sends normal REST/OAuth traffic to same-origin `/api/*` and `/oauth/*`
URLs. The generated Netlify `_redirects` file proxies those requests to the VPS.
This lets the browser keep one frontend origin for normal application actions.

### The VPS owns all trading state

The API, worker and PostgreSQL live on the dedicated VPS. `ManagedAccount.enabled`
and the backend execution status are authoritative. Closing a phone/browser tab has
no effect on the worker. Auto Trading continues until an explicit backend stop/risk
condition changes the account state.

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

The production frontend does not depend on the former 15-second dashboard polling
cycle and does not route a long-lived SSE stream through the static frontend.

1. The browser requests `/api/me/live-ticket` through the same-origin Netlify REST
   proxy.
2. FastAPI verifies the current server session and returns a signed ticket valid for
   45 seconds.
3. The browser opens `wss://<backend>/ws/me/live?ticket=...` directly to the VPS.
4. FastAPI verifies the ticket, the still-valid server session, the managed account
   ID and the browser Origin.
5. Runtime/trade changes produce combined account snapshots that update the existing
   DOM directly without rebuilding the whole dashboard.
6. If WSS reconnects, a bounded five-second HTTP snapshot fallback keeps the last
   rendered dashboard usable. Navigation is never blocked by that fallback.

The WebSocket ticket contains no Deriv OAuth token and cannot be used to purchase a
contract. Deriv credentials stay encrypted on the backend.

## Start and Stop

Start and Stop remain short backend commands. The browser does not wait for the
worker to finish connecting before regaining control of the UI.

- Start writes `enabled=true` and `starting`.
- The worker independently establishes/reuses the account private WebSocket.
- Realtime state moves to Waiting/Executing/Running as the worker progresses.
- Stop writes the backend stop state. Closing a tab does not call Stop.

## Trading transport resilience

### Proposal timeout

A proposal request is non-financial. The split worker bridge permits one short retry
for a temporary proposal/session transport interruption.

### BUY acknowledgement timeout

A BUY is financial. It is never blindly retried after an acknowledgement timeout,
because the provider may have accepted the purchase even if the response was lost.
That account fails closed for reconciliation instead of risking a duplicate BUY.

### Private account session interruption

A temporary pre-purchase private-session interruption does not permanently disable
Auto Trading. The account leaves the current hot execution set, remains enabled,
reconnects independently, and becomes eligible again only after its authenticated
session is ready.

### Public market stream

Real provider/network failures retain bounded exponential/rate-limit backoff.
Intentional `custom_market_set_changed` service restarts use a separate fast reconnect
path so adding/removing strategy markets does not create the former ~18-second gap.

## Dashboard work cannot delay settlement

Worker-to-API dashboard wake-ups are now best-effort background events. Contract
persistence and settlement do not await the frontend notification. If that internal
wake-up is unavailable, the realtime WebSocket checks durable database revisions and
recovers automatically.

The former global `/metrics/summary` builder is not part of the Netlify Custom
Strategy frontend. In `FRONTEND_HOSTING_MODE=netlify`, global dashboard dirty calls
no longer trigger that expensive aggregate rebuild.

## Deployment configuration

### Netlify

Set after the new backend has a stable HTTPS hostname:

```text
BACKEND_ORIGIN=https://api.your-domain.example
```

Optional override:

```text
DASHBOARD_WS_BASE_URL=wss://api.your-domain.example
```

`npm run build` produces `dist/`, injects the frontend/realtime scripts, appends the
final compact mobile CSS, and writes `_redirects` for `/api/*` and `/oauth/*`.

### Dedicated VPS

Create `/root/legacy-model/.env` from `.env.vps.example` and configure at minimum:

- PostgreSQL password
- Deriv app/OAuth settings
- token encryption key
- control API key
- realtime signing key
- Netlify/custom frontend origins
- backend trusted hostname
- Demo/Real execution switches

The VPS Compose stack intentionally contains only:

```text
database
api
worker
```

There is no production frontend container.

## VPS selection stage

Provider selection is deliberately deferred until the code/release gate is complete.
At that stage compare current plans by CPU class, NVMe storage, memory, network,
region/provider latency, snapshot/backup options and support rather than reusing the
current Hostinger environment by default.

## Acceptance gates before real-money use

1. Netlify static build completes and loads without the VPS serving UI assets.
2. OAuth returns to the Netlify frontend and creates a working server session.
3. `/api/me` and Start/Stop stay bounded and responsive.
4. Direct WSS reaches Connected and survives temporary disconnect/reconnect.
5. One Demo account produces:
   `CUSTOM_STRATEGY_SIGNAL_QUALIFIED -> PURCHASE_EXECUTION_REQUEST -> PURCHASE_CONFIRMED -> CONTRACT_SETTLED`.
6. Closing the browser for several trades does not change `ManagedAccount.enabled`.
7. Reopening the browser reconstructs current runtime/trade state from the backend.
8. A deliberate public market-set restart reconnects quickly without outage backoff.
9. A dashboard wake-up failure cannot delay contract settlement.
10. Only after these Demo gates pass should Real execution be enabled on the new VPS.
