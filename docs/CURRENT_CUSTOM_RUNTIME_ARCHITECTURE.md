# Current Custom Strategy Runtime Architecture

This document describes the production execution path introduced for the current Custom Strategy Builder runtime. It is deliberately scoped to the current application and does not depend on historical deployments.

## Production entry point

`docker-compose.yml` starts the trading worker with:

```text
python -m app.custom_strategy_worker
```

The production custom runtime is account-scoped:

```text
Start Auto Trading
  -> validate enabled managed account
  -> validate stored trade-scope credential
  -> synchronize composite runtime key into client execution state
  -> establish exact account private Deriv WebSocket session
  -> validate Custom Strategy ownership/configuration
  -> WAITING_FOR_CONDITION
  -> subscribe only markets required by active custom accounts
  -> evaluate bounded in-memory market windows
  -> CUSTOM_STRATEGY_SIGNAL_QUALIFIED
  -> AccountExecutionSession
  -> Deriv proposal
  -> validate proposal economics
  -> PURCHASE_EXECUTION_REQUEST using that proposal ID on that exact account session
  -> PURCHASE_CONFIRMED
  -> persist trade/contract
  -> subscribe exact contract for settlement
  -> settlement updates durable trade/risk state
```

If account preparation fails, the account is disabled before scanning and the backend execution status becomes `ERROR`. A missing client-state key is therefore a start/preparation failure, not an exception that is swallowed during a purchase loop.

## Runtime lifecycle

The authoritative backend states are:

- `STOPPED`
- `STARTING`
- `WAITING_FOR_CONDITION`
- `EXECUTING`
- `RUNNING`
- `ERROR`

`enabled=true` alone is not proof that execution is running. `Start Auto Trading` first writes `STARTING`; only the worker can advance the account after credential, client-state, session, configuration, and market preparation succeed.

When no account is execution-ready, the public custom scanner is not started. When an account is stopped or otherwise removed from the active execution set, its custom scan task and custom market state are removed. A private account session may remain only as long as it is required to finish monitoring an already purchased open contract.

## Hot-path performance rules

The custom tick handler intentionally does not:

- persist every tick;
- write bot state every tick;
- query trades/open contracts on every tick;
- emit `EVERY_TICK` at INFO;
- request a proposal before a user condition qualifies.

Last-digit, percentage-window, and tick-direction conditions are evaluated from bounded in-memory deques populated from the subscribed market stream and bounded history bootstrap.

The final `/me/execution-alert` compatibility route no longer scans global signal/decision/trade history. `/me/execution-runtime` is the lightweight account-scoped status endpoint used by the UI.

A Custom Strategy save is one server write request. Strategy, execution settings, martingale preferences, selection, risk reset, and stopped lifecycle state are written in one database transaction. The endpoint logs `CUSTOM_STRATEGY_SAVE_TIMING`.

## Retained shared infrastructure

The custom worker intentionally keeps small shared components that are not strategy routers:

- account credential discovery and reenrollment;
- exact-account private WebSocket sessions;
- public WebSocket resilience and provider rate-limit protection;
- managed-account lifecycle and account-mode lock;
- trade registration idempotency;
- unresolved-contract settlement safety;
- account risk/recovery persistence;
- manual custom martingale configuration;
- virtual-protection persistence and settlement;
- profit/account snapshot accuracy;
- database repositories and models.

These components are reusable account/session/persistence utilities; they do not rotate accounts or choose Custom Strategy signals.

## Legacy strategy/execution modules removed from the production custom path

The new production worker does **not** import or install the previous strategy-routing chain, including:

- `app.custom_strategy_runtime`
- `app.shared_system_strategy_clock`
- `app.rotating_execution_cohorts`
- `app.scalable_group_execution`
- `app.guaranteed_signal_delivery`
- `app.standardized_execution_runtime`
- `app.multi_strategy_concurrency`
- `app.strategy_v2_runtime`
- `app.multi_strategy_runtime`
- `app.production_worker_integration`
- tick persistence/logging installers used by the previous worker
- RF/AIDR strategy scanner/install chains used by the previous worker

Those modules are intentionally not physically deleted in this critical fix where they are still referenced by compatibility routes, historical tests, release tests, or rollback tooling. Removing files merely because their names look obsolete would be unsafe. The production reachability boundary is established by the new worker entry point and is enforced by regression tests that fail if the banned legacy purchase-router imports return.

A later repository-cleanup change can physically delete modules only after their remaining API/test/rollback references are migrated or removed and the full release gate is green.
