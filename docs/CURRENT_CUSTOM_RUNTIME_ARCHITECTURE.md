# Current Custom Strategy Runtime Architecture

This document describes the current production Custom Strategy runtime on the full VPS deployment.

## Production entry points

The API runs as:

```text
app.vps_backend_api:app
```

The worker runs as:

```text
python -m app.custom_strategy_worker
```

The public browser edge is Caddy. Frontend, API, WebSocket gateway, worker and PostgreSQL all run on the VPS.

## Account-scoped execution path

```text
Start Auto Trading
  -> admit enabled managed account
  -> prepare exact account credential/session state
  -> establish authenticated private Deriv WebSocket
  -> validate Custom Strategy configuration
  -> WAITING_FOR_CONDITION
  -> subscribe required markets
  -> evaluate bounded in-memory market windows
  -> CUSTOM_STRATEGY_SIGNAL_QUALIFIED
  -> proposal on the exact account session
  -> validate proposal economics
  -> BUY exact proposal ID
  -> PURCHASE_CONFIRMED
  -> persist trade/contract
  -> monitor exact contract
  -> settle
  -> update durable account/recovery state
```

An uncertain BUY acknowledgement is never blindly retried. The runtime reconciles the account/contract state before permitting another financial purchase.

## Lifecycle invariant

After a successful explicit Start, the account remains enabled until one of these terminal events occurs:

- Take Profit is reached.
- Stop Loss is reached.
- The user explicitly presses Stop.

Preparation, credential refresh, provider connection, OTP, proposal, temporary balance, contract registration, contract reconciliation, database and runtime failures are **recovery states**, not automatic Stop events.

The final lifecycle authority converts automatic `error`, `stopped`, `disabled`, `inactive` and related synthetic states back to retry/reconnect states for an already-running account unless TP, SL or the durable manual hard-stop sentinel applies. A periodic worker repair scan also restores direct database automatic disables that bypass normal setter wrappers.

Explicit manual Stop is enforced independently by the durable hard-stop sentinel at the final pre-BUY boundary. This protects the user's Stop even if account-row cleanup or another request is delayed.

## Runtime states

Operational states can include:

- `STARTING`
- `WAITING_FOR_CONDITION`
- `RUNNING`
- `EXECUTING`
- `RECONNECTING`
- other bounded waiting/recovery states

`take_profit` and `stop_loss` are deliberate terminal financial states. A genuine manual Stop is represented by the independent hard-stop sentinel and corresponding stopped lifecycle state.

## Connection and recovery behavior

Private account WebSocket sessions are account-scoped. Provider/network interruptions reconnect the affected account without rebuilding healthy sibling sessions.

Browser-to-VPS execution takeover is targeted to the account whose browser lease expired. It does not perform provider-wide account validation or rebuild all sibling sessions.

OTP/private WebSocket bootstrap uses bounded concurrency and lets the shared provider broker own the request timeout/retry boundary. Provider rate-limit backoff remains authoritative.

Stale or historical unresolved contracts are quarantined/reconciled per account so one old contract cannot globally lock healthy execution.

## Hot-path performance rules

The custom tick handler intentionally does not:

- persist every tick;
- write bot state every tick;
- query trades/open contracts on every tick;
- emit every tick at INFO level;
- request a proposal before a user condition qualifies.

Last-digit, percentage-window and tick-direction conditions are evaluated from bounded in-memory market state.

## Shared infrastructure retained

The worker retains reusable components for:

- exact-account credential/session discovery;
- private WebSocket lifecycle and provider rate-limit protection;
- public market WebSocket resilience;
- trade registration idempotency;
- unresolved-contract settlement safety;
- account risk/recovery persistence;
- manual stake/recovery configuration;
- virtual-protection persistence and settlement;
- account/balance accuracy;
- database repositories and models.

These utilities support the Custom Strategy runtime; they do not override the user's strategy qualification conditions.

## Deployment boundary

The production stack is defined by:

- `docker-compose.yml`
- `docker-compose.vps.yml`
- `Caddyfile`
- `scripts/build-vps.mjs`
- `scripts/deploy_full_vps.sh`
- `app.vps_backend_api`
- `app.custom_strategy_worker`

The release gate compiles and tests the VPS-native API/realtime bootstrap, the TP/SL/manual-only lifecycle authority, provider connection resilience and the production frontend build before a release can reach `main`.
