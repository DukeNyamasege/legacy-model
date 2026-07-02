# Test 2 Implementation Report

## Outcome

The previous bot was a single-file Over/Under process using JSON as active state,
immediate streak purchases, one global trade lock, and a fixed 15-tick cooldown.
It already used Deriv's new public WebSocket, REST account/OTP endpoints, bulk
purchase, and private open-contract subscriptions.

The active strategy is now The Underdog Legacy Model `2.0.0-test2`:

- `LE4x2` is the only signal.
- `DIGITOVER` with barrier `3` is the only contract.
- Symbol is `1HZ100V`.
- Stake is fixed at `$0.35 USD`.
- Duration is one tick.
- HMM and Bayesian decisions begin in shadow mode.
- Under and `GE5` have no executable source path.
- The requested session/hour/day/open-contract hard limits are not configured or
  evaluated.

## Trade Flow

1. Store each completed tick with epoch, tick ID, quote, final digit, sequence,
   and connection ID.
2. Create one candidate for the first low-low pair in a continuous low run.
3. Persist the candidate before any API request.
4. Reject it if a new tick or reconnect invalidates one-tick alignment.
5. Request a current Deriv proposal using `underlying_symbol`.
6. Require proposal ID, ask price, and payout; parse string or numeric values.
7. Calculate break-even probability, expected value, and return on stake.
8. Record HMM and Bayesian shadow outputs.
9. Atomically consume the signal once.
10. Submit the current REST bulk-purchase shape with PAT/account pairs.
11. Persist every purchased contract and subscribe to its private status stream.
12. Settle idempotently, update Bayesian evidence, P/L, drawdown, streak state,
    adaptive cooldown, exports, and live balance.

## State And Services

PostgreSQL is the deployment source of truth. SQLite is available for isolated
local testing. The schema includes runs, ticks, candidates, decisions, proposals,
trades, streaks, bot state, account snapshots, model artifacts, audit events, and
trader leases. Signal consumption, settlement, and lease ownership use database
transactions and uniqueness constraints.

FastAPI provides:

- `GET /health/live`
- `GET /health/ready`
- `GET /status`
- `GET /metrics/summary`
- `GET /metrics/recent-trades`
- `GET /metrics/model`
- `POST /control/pause`
- `POST /control/resume`
- `POST /control/emergency-stop`

Control calls require `CONTROL_API_KEY`. The API never starts a trading operation
directly. The local launcher supervises the separate API and worker processes.

## Test 1 Reset

The four unresolved legacy IDs were reconciled through Deriv before reset:

- `3884274759`: won, `+0.20`
- `3895119499`: lost, `-0.35`
- `3885928719`: lost, `-0.35`
- `3892376479`: won, `+0.20`

Test 1 was archived at `archives/test1_20260702T093943Z`. The archived JSON was
sanitized before being made read-only. Test 2 currently has zero candidates,
zero trades, zero wins/losses, and `$0.00` P/L. Clean exports exist under
`exports/test2`.

## Verification Results

- Python compilation: passed.
- YAML parsing for config, Render, and Compose files: passed.
- Alembic upgrade: passed on local SQLite.
- Automated suite: 13 tests passed.
- PAT account lookup: passed; two accounts returned.
- OTP private WebSocket URL: passed.
- Public WebSocket and active symbols: passed; 78 symbols returned.
- Live Test 2 proposal: passed; ask `0.35`, payout `0.55`, break-even `0.636364`.
- Full worker startup with trading disabled: passed; public/private sockets and
  trader lease connected, then shut down cleanly with zero trades.
- Desktop and narrow dashboard rendering: visually verified.
- Docker build: not run because Docker is not installed on this machine.
- Render deployment: prepared, not submitted.
- VPS deployment: prepared, not submitted.

## Deployment

Render uses `render.yaml`, `Dockerfile`, one web service, one background worker,
and shared PostgreSQL. Render environment variables are documented in
`README_TEST2_DEPLOYMENT.md`.

VPS uses `docker-compose.yml`, `docker-compose.vps.yml`, PostgreSQL and model
volumes, one worker replica, health checks, backup/restore scripts, and
`restart: unless-stopped`. Variables and operations are documented in
`README_VPS_DEPLOYMENT.md`.

## Security

- The PAT remains only in the ignored local `tokens.txt`.
- `tokens.txt` was removed from Git tracking without deleting the local file.
- Tokens are absent from state, database rows, logs, API responses, exports,
  fixtures, deployment files, and the Test 1 archive.
- Account IDs are masked in logs, API responses, dashboard data, and trades.
- Real trading requires all explicit real-mode environment acknowledgements.
- Source startup fails if a PAT-shaped secret is found in source/config/docs.
- The PAT was previously exposed and exists in repository history; rotate it
  before production use.

## File Inventory

Major modified files:

- `.gitignore`
- `.env.example`
- `config.yaml`
- `enhanced_bot.py`
- `local_dashboard.py`
- `dashboard/index.html`
- `requirements.txt`
- `test_strategy_logic.py`
- `verify_token.py`
- `full_verify.py`
- `README.md`

New application files:

- `app/api.py`
- `app/worker.py`
- `app/config.py`
- `app/database.py`
- `app/models.py`
- `app/strategy/signal_detector.py`
- `app/strategy/over3_strategy.py`
- `app/strategy/decision_engine.py`
- `app/strategy/cooldown.py`
- `app/model/bayesian_probability.py`
- `app/model/feature_builder.py`
- `app/model/hmm_regime.py`
- `app/model/model_store.py`
- `app/repositories/test2_repository.py`
- `app/services/analytics_service.py`

New operations and deployment files:

- `scripts/reset_test_data.py`
- `scripts/export_test2.py`
- `scripts/deploy_vps.sh`
- `scripts/backup_database.sh`
- `scripts/restore_database.sh`
- `migrations/env.py`
- `migrations/versions/20260702_0001_test2_schema.py`
- `alembic.ini`
- `Dockerfile`
- `.dockerignore`
- `render.yaml`
- `docker-compose.yml`
- `docker-compose.vps.yml`
- `.env.vps.example`
- `README_TEST2_DEPLOYMENT.md`
- `README_VPS_DEPLOYMENT.md`

## Remaining Limitations

- Docker and PostgreSQL containers still need a real build/integration run on a
  machine with Docker.
- Render and VPS deployments require external credentials and have not been
  executed.
- Real-money mode has intentionally not been tested.
- The maintainable HMM is a categorical three-state framework; it remains
  `NOT_READY` before 5,000 Test 2 ticks and its gate must not be enabled without
  reviewing validation evidence.
- The local SQLite lease is for testing; PostgreSQL row locking is the production
  duplicate-instance mechanism.

## Production Checklist

- Rotate the exposed PAT and update only the deployment secret.
- Install Docker and run `docker build .`.
- Run the stack against PostgreSQL and repeat the full suite.
- Confirm Render Blueprint cost before creation.
- Keep demo mode and both model modes on `shadow`.
- Verify `/health/ready`, worker logs, account balance, and lease ownership.
- Review at least 300 settled Test 2 signals before considering Bayesian gate.
- Collect at least 5,000 ticks and validate HMM state mapping before HMM gate.
- Back up PostgreSQL before every deployment change.
