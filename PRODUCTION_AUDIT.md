# Production Architecture and Deployment Audit

Audit updated: 2026-08-20

## 1. Authoritative production topology

`derivadmin.site` is a full-VPS application.

```text
Internet
  -> Caddy
     -> frontend 127.0.0.1:8081
     -> API/OAuth/WebSocket 127.0.0.1:8080

Docker Compose
  -> frontend
  -> api: app.vps_backend_api:app
  -> worker: python -m app.custom_strategy_worker
  -> PostgreSQL
```

Caddy is the public HTTPS/WSS edge. Application containers remain on Docker/loopback networking.

## 2. Authentication and account linking

1. Browser authentication starts through the server OAuth route.
2. OAuth state and PKCE verifier material are generated and validated server-side.
3. The callback is `https://derivadmin.site/oauth/callback`.
4. Account credentials and session material remain server-side; reusable Deriv trading credentials are not returned to browser JavaScript.
5. Linked account identity is canonicalized before execution state is created.

## 3. Worker execution path

For an enabled Custom Strategy account:

```text
strategy qualification
  -> exact account private WebSocket
  -> proposal
  -> validate economics
  -> BUY exact proposal ID
  -> persist contract
  -> monitor proposal_open_contract
  -> settlement
  -> durable account/recovery update
```

An uncertain BUY acknowledgement is reconciled before another financial purchase. It is never blindly retried.

## 4. Terminal lifecycle authority

Once the user has successfully started Auto Trading, only these events may terminate execution:

1. Take Profit reached.
2. Stop Loss reached.
3. Explicit user Stop.

The manual Stop path is protected by an independent durable hard-stop sentinel and a final pre-BUY fence.

The following are non-terminal recovery conditions for an already-running account:

- provider/network disconnect;
- OTP/bootstrap failure;
- credential/session refresh problem;
- proposal failure;
- temporary insufficient balance response;
- contract unavailable/registration problem;
- ambiguous or unresolved contract state;
- database/runtime exception;
- synthetic `error`, `stopped`, `disabled` or `inactive` state produced by an automatic path.

The final lifecycle authority keeps the account enabled and moves these conditions to retry/reconnect/waiting behavior unless TP, SL or the manual hard-stop sentinel applies. A periodic repair loop also restores automatic database disables that bypass normal setters.

## 5. Connection resilience

- Private WebSocket bootstrap is account-scoped and bounded.
- Provider rate-limit backoff remains authoritative.
- Ordinary network reconnects do not rebuild healthy sibling sessions.
- Browser-to-VPS ownership takeover is targeted to the affected account.
- The pooled OTP broker owns the bounded request/retry boundary; the low-latency wrapper no longer cancels normal OTP work after the historical short timeout.
- Historical unresolved contracts are quarantined/reconciled without globally locking healthy accounts.

## 6. Realtime dashboard

The browser obtains a short-lived account/session-bound ticket and connects to:

```text
wss://derivadmin.site/ws/me/live
```

Realtime snapshots are account-scoped. Browser lifetime does not control the trading worker.

The VPS gateway treats a send attempted after a normal client disconnect/close as a completed disconnect, preventing the historical `websocket.send` after `websocket.close` ASGI race from becoming an application traceback. Unexpected realtime processing errors are still logged.

## 7. Database and settlement safety

- PostgreSQL uses a persistent named volume.
- Trade registration and settlement are idempotent.
- Duplicate settlement cannot double-count a contract.
- Open/unresolved contract state is reconciled account-by-account.
- Clear Trades is history presentation/reset behavior only and must not erase active recovery/financial execution state.
- Deployment never uses `docker compose down -v`.

## 8. Deployment safety

`scripts/deploy_full_vps.sh`:

1. validates prerequisites and Compose;
2. compiles source;
3. builds frontend/API/worker candidate images before cutover;
4. verifies PostgreSQL;
5. creates a pre-deploy PostgreSQL dump;
6. applies Alembic migrations;
7. recreates API, worker and frontend;
8. verifies service health;
9. validates/reloads Caddy when required.

The release gate additionally tests the VPS-only architecture, realtime disconnect guard, provider continuity and TP/SL/manual-only lifecycle authority.

## 9. Required production evidence

A healthy trade cycle should show qualification, proposal, confirmed BUY and settlement without a fatal API/worker traceback.

Useful checks:

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS https://derivadmin.site/backend-health
```

External provider, network or database failures cannot be made impossible. The production requirement is that such failures remain recoverable and do not automatically stop a running account.
