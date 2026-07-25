# Legacy Model — RF-PUT5 AI

This repository contains the current **Father of Automation Series** Deriv trading application. It is a Python/FastAPI service with a continuously running RF-DIR5 worker, PostgreSQL persistence, a responsive dashboard, per-account AutoTrade controls, and an account-independent system/model performance ledger.

> **Risk warning:** recovery/Martingale sizing does not guarantee recovery. A recovery contract can lose, provider payouts can change, and real-money trading can lose the full amount staked.

## Current production shape

The Docker stack contains three services:

| Service | Current role |
| --- | --- |
| `database` | PostgreSQL 17 persistence for signals, proposals, user contracts, virtual runs, risk state, OAuth sessions, and canonical model trades. |
| `api` | FastAPI dashboard/API service on container port `8080`; it applies Alembic migrations before startup. |
| `worker` | A single `RFDir5TradingBot` process that consumes market ticks, evaluates signals, records the model ledger, and executes eligible account trades. |

All services use `restart: unless-stopped`. The VPS override pins the API to `10.89.0.10` for Caddy and keeps one worker replica. Caddy serves the public HTTPS application at the configured domain (currently `derivadmin.site`).

## Current strategy

The worker entry point is `python -m app.worker`, which starts `RFDir5TradingBot`; it does **not** start the older digit-pattern bot directly.

The checked-in RF strategy configuration is:

- model: `RF-PUT5 AI`, version `8.0.0-rf-put5-ai-bayesian-hmm`;
- strategy identifier: `RF-PUT5-AI-V8`;
- direction: **FALL** using Deriv `PUT` contracts;
- duration: **5 ticks**;
- analysis: six quotes producing five completed movements;
- configured RF markets: `R_10`, `R_100`, `R_75`, `1HZ10V`, and `1HZ75V`;
- minimum history: 100 movements;
- directional requirement: at least three of five moves, including at least two recent directional moves;
- one open strategy contract globally and one open contract per account;
- candidate arbitration window: 75 ms;
- stale signal limit: 1,800 ms;
- no artificial minimum interval between trades.

The RF decision engine also evaluates efficiency, normalized impulse, move concentration, proposal economics, Bayesian evidence, and directional HMM state. The strict and cadence-relaxed thresholds are defined in `config.yaml`. The top-level legacy `strategy`/`signal` digit configuration remains in the shared configuration schema, but the deployed worker is the RF-DIR5 worker described above.

## Demo and real execution

The checked-in defaults are deliberately demo-first:

- `deriv.environment: demo`;
- Deriv trading integration enabled;
- Demo and Real account UI/execution paths available;
- `allow_real_trading: false`.

Real mode is guarded. If the configured Deriv environment is `real`, startup requires all of the following:

```text
TRADING_MODE=real
ALLOW_REAL_TRADING=true
PRODUCTION_ACKNOWLEDGEMENT=I_ACKNOWLEDGE_REAL_MONEY_TRADING
```

Without that exact acknowledgement, configuration validation rejects real-money startup.

Users authenticate through Deriv OAuth 2.0 with PKCE and server-side state verification. OAuth/account tokens and optional personal trading API tokens are stored encrypted; the browser session receives an HTTP-only session cookie rather than the stored trading credential. Demo and Real sibling accounts retain separate balances, execution state, settings, and contract history.

## Personal AutoTrade behavior

Each managed account has independent:

- enabled/disabled AutoTrade state;
- Demo or Real account mode;
- base stake;
- take-profit and stop-loss controls;
- Martingale toggle;
- execution health/status;
- open-contract limit;
- recovery debt and virtual-protection state.

A manual AutoTrade stop resets the account's active recovery state. Resuming starts from the configured base stake rather than an old recovery stake; historical trades, wins, losses, and P/L are retained. Page refreshes, WebSocket reconnects, API restarts, and worker restarts are not treated as manual stop actions.

When Deriv rejects a stored personal API token as expired or invalid, the worker
disables affected execution, removes that rejected PAT from encrypted storage
(including Demo/Real siblings sharing it), and preserves the owner's OAuth
identity. The dashboard displays the rejection reason and restores the API-token
input so the owner can verify and save a new active token before rejoining
AutoTrade. Transient timeouts and network errors do not erase credentials.

## Virtual-loss protection

Virtual protection is per account and uses fixed safety invariants:

1. A real win resets the consecutive-real-loss counter.
2. Exactly **two consecutive purchased-contract losses** enter virtual mode.
3. Virtual observations make no Deriv purchase and have zero monetary impact.
4. A virtual loss resets the consecutive-virtual-win counter to zero.
5. Exactly **two consecutive virtual wins** leave virtual mode.
6. The next qualifying event is the real recovery trade.

Virtual losses never add recovery debt. Only losses from purchased Demo/Real contracts add debt. The current recovery planner uses the recorded debt and the current proposal profit ratio, rounds the required stake to cents, respects the configured balance reserve, and skips an account when the full required trade is unaffordable.

## Canonical system/model ledger

Global statistics do not come from a user, master account, or sum of copied contracts. A qualifying model event creates one `SystemModelTrade`, regardless of how many users receive the signal. The worker records:

- unique signal ID and market;
- direction and contract type;
- entry/expiry tick sequence;
- entry/exit spot;
- real or virtual classification;
- signal, entry, and settlement timestamps;
- outcome and reference economics;
- fixed-stake and recovery simulation fields.

`SystemModelState` persists the model's independent real/virtual state. Due model entries settle from market ticks even when no personal account is currently trading. Unresolved, non-virtual ledger entries are the source of the dashboard's **Open Trades** figure.

Global model statistics exclude virtual observations from trades, wins, losses, win rate, P/L, and winning/losing streaks. Therefore, for settled real model events:

```text
total_trades = wins + losses
win_rate = wins / total_trades
```

The dashboard summary and `/metrics/system-performance` both read this canonical ledger.

## Model P/L and stake simulation

Permanent Global Model Statistics use a reference base stake of **USD 0.50** and replay the same real model outcomes in parallel:

- **Without Martingale:** every real model trade uses the selected flat base stake.
- **With Martingale:** the stake is selected before the result using carried recovery debt, the recorded proposal profit ratio, cent rounding, and the project's recovery rules.

The dashboard's simulator is read-only and supports base stakes from `$0.50` through `$1,000`. It replays ledger history for the selected stake; it does not update a user's live stake, account settings, or the permanent `$0.50` reference figures. Failed simulation requests display unavailable data rather than a false zero.

## Dashboard

The Global dashboard currently shows:

- registered traders and currently active traders;
- canonical model trades today;
- currently open real model trades;
- a live day-close countdown;
- Today's model P/L with and without Martingale;
- Today's real model trades, wins, losses, and win rate;
- Yesterday, This Week, and This Month model P/L;
- all-time longest real winning and losing streaks;
- worker/model status and uptime;
- Recent Contracts with All, Actual, and Virtual filters.

Decorative sparklines, duplicate Today period output, Global drawdown cards, the current-loss-streak card, and the explanatory statistics footer have been removed. Recent Contracts and personal-account data remain separate from canonical model statistics.

The reporting timezone is selected in this order:

1. `TRADING_REPORT_TIMEZONE`;
2. `DASHBOARD_TIMEZONE`;
3. `Africa/Nairobi` (default).

An invalid timezone falls back to UTC. The backend sends the authoritative next-session-close timestamp, and the browser updates the countdown once per second.

## API overview

Public/application routes include:

- `/`, `/runtime`, `/status`, and `/api/status`;
- `/health/live`, `/health`, and `/health/ready`;
- `/oauth/start` and `/oauth/callback`;
- `/me` plus account switch, AutoTrade, resume, settings, API-token, and logout operations;
- `/metrics/summary`, `/metrics/recent-trades`, `/metrics/model`, and `/metrics/rf-strategy`;
- `/metrics/system-trades` and `/metrics/system-performance`;
- `/ws/dashboard` for live summary snapshots.

Control and account-administration routes require control authentication. The browser renders WebSocket snapshots directly, uses a watchdog plus bounded exponential reconnect backoff with jitter, and retains periodic REST refresh as reconciliation rather than refreshing the entire page for every snapshot.

## Persistence and migrations

PostgreSQL is the production database. SQLAlchemy models and Alembic migrations cover, among other records:

- test runs and bot state;
- market ticks and candidate/directional signals;
- proposals and purchased contracts;
- managed accounts, snapshots, and client sessions;
- OAuth login state and encrypted credentials;
- account risk state and virtual trades;
- canonical system model trades and `SystemModelState`;
- leases and audit events.

The API and worker both run `alembic upgrade head` before starting. Do not delete historical model trades at midnight: Today is a timezone-bounded query, while Yesterday, Week, Month, and all-time streaks continue to use retained history.

## Local checks

Install Python dependencies and run the test suite:

```bash
python -m pip install -r requirements.txt
python -m unittest discover
```

Validate the dashboard preview build:

```bash
npm ci
npm run build
```

The Netlify build is a dashboard preview only. `DASHBOARD_API_BASE_URL`, when set, must be an HTTPS origin without a path, query string, or fragment:

```bash
DASHBOARD_API_BASE_URL=https://your-api.example npm run build
```

## VPS deployment

Use an Ubuntu host with Docker Engine, Docker Compose, Caddy, DNS, and the required secrets. See `README_VPS_DEPLOYMENT.md` for the operational procedure.

Typical deployment:

```bash
# Create .env from your private deployment-secret template, then edit it.
nano .env
chmod +x scripts/*.sh
./scripts/deploy_vps.sh
docker compose -f docker-compose.yml -f docker-compose.vps.yml logs -f --tail=100 worker
```

The repository intentionally does not contain production secrets. At minimum, deployment must provide the PostgreSQL password, Deriv application/OAuth configuration, token-encryption key, control API key, and any trading or Telegram credentials used by the deployment.

Useful operations:

```bash
curl -i https://derivadmin.site/health
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
docker compose -f docker-compose.yml -f docker-compose.vps.yml logs -f worker
./scripts/backup_database.sh
```

## Important files

| Path | Purpose |
| --- | --- |
| `app/worker.py` | Starts the single RF-DIR5 worker. |
| `app/rf_dir5_bot.py` | Market processing, RF decisions, canonical ledger hook, and account execution orchestration. |
| `app/repositories/test2_repository.py` | Primary persistence, summaries, canonical ledger, accounts, and metrics. |
| `app/repositories/rf_dir5_repository.py` | RF signals, account risk/recovery, virtual observations, and stake planning. |
| `app/api.py` | FastAPI dashboard, OAuth, personal controls, metrics, health, and WebSocket endpoints. |
| `app/models.py` | SQLAlchemy schema. |
| `config.yaml` | Checked-in defaults; supported environment variables override selected values. |
| `dashboard/index.html` | Dashboard UI and browser-side live update logic. |
| `migrations/versions/` | Alembic history. |
| `docker-compose.yml` | PostgreSQL, API, and worker production stack. |
| `docker-compose.vps.yml` | VPS networking and single-worker overrides. |
| `scripts/deploy_vps.sh` | Build, migrate, and deploy workflow. |
| `netlify.toml` | Optional dashboard-preview build configuration. |

## Security and operations notes

- Never commit `.env`, Deriv tokens, OAuth tokens, PostgreSQL credentials, control keys, or Telegram secrets.
- Keep the VPS database and API backups private.
- Keep `ALLOW_REAL_TRADING=false` unless intentionally promoting a validated deployment with the exact production acknowledgement.
- Use HTTPS for the public dashboard and OAuth callback.
- Review logs for worker lease, tick silence, reconnect, purchase, and settlement errors.
- The health endpoint verifies database access and worker heartbeat; readiness also reports stale or missing worker state.
