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

All services use `restart: unless-stopped`. Docker publishes the API only on
`127.0.0.1:8080`, the VPS override keeps one worker replica, and Caddy serves
the public HTTPS application at the configured domain (currently
`derivadmin.site`).

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

Deriv PATs are shared credentials for the linked Options identity: one verified
PAT may authorize both the Demo and Real Options accounts returned by Deriv.
The application verifies the PAT against the currently selected account, then
attaches that same credential to every matching Demo/Real sibling. The accounts'
balances, settings, histories, and risk state remain separate even though the PAT
is shared.

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

## RF-PUT5 bulk execution

RF-PUT5 purchases use Deriv's V2 REST bulk-purchase endpoints exclusively:
`/trading/v1/options/contracts/bulk-purchase/demo` and `/real`. OAuth remains
the login and account-discovery mechanism; an encrypted PAT with `trade` scope
is the execution credential. An account without a PAT remains visible but is
excluded with `bulk_execution_pat_required`. PATs are never included in logs or
audit payloads.

Before every signal, account virtual/risk eligibility and the shared recovery
stake calculation are evaluated afresh. Eligible accounts are grouped by Demo
or Real mode, current Martingale setting, and exact cent-rounded stake. Each
group is deterministically ordered, split into at most 100 accounts, and all
resulting shards are dispatched concurrently. A random eligible member is
recorded as diagnostic leader for each shard; it does not authenticate anyone
else and cannot block execution. Partial responses register only successful
contracts. Missing or failed members are not retried through the legacy
WebSocket buy path, preventing ambiguous duplicate financial purchases.

`bulk_execution_batches` and `bulk_execution_members` retain token-free audit
metadata, request latency, member status, returned contract economics, and
within-shard consistency measurements. Deriv controls the registered App ID's
3% markup economics. The application neither adds nor subtracts markup during
bulk purchase or settlement: returned `Trade.profit` is authoritative and
`app_markup_amount` remains informational.

## Dashboard performance data sources

- **With Martingale** is observed accounting: one deterministic representative
  from the largest group of Martingale accounts with an identical ordered
  settled execution signature (`signal_id`, actual buy price, payout, profit,
  and outcome). Its P/L is the representative's summed `Trade.profit`; it is
  never multiplied by cohort size.
- **Maximum Stake** is the largest actual `Trade.buy_price` on that same
  dominant execution trajectory. It is not reconstructed from recovery debt.
- **Without Martingale** remains the canonical flat-stake calculation based on
  normalized realized model economics.
- **Simulate Your Stake** remains a hypothetical replay. Its explicitly named
  simulated fields use the same pre-trade recovery calculator as production,
  but cannot overwrite observed dashboard fields.
- **Personal Account** remains that managed account's actual Deriv balance and
  settled contracts. It equals observed system P/L only when the account's full
  period execution signature belongs to the dominant cohort.

Execution-time Martingale membership, stake, and proposal ratio are taken from
the existing bulk batch audit. Older rows without bulk metadata cautiously use
the managed account's current Martingale setting and require stable
`managed_account_id`; masked account identifiers are never used as cohort keys.

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

Global statistics do not come from a user, master account, or sum of copied
contracts. A qualifying signal creates one `SystemModelTrade` only after at
least one real Deriv contract has been registered successfully, regardless of
how many accounts purchase it. Virtual-only, waiting, skipped, and failed
signals remain diagnostic/account events and do not enter the monetary ledger.
The worker records:

- unique signal ID and market;
- direction and contract type;
- entry/expiry tick sequence;
- entry/exit spot;
- real execution classification;
- signal, entry, and settlement timestamps;
- outcome and reference economics;
- fixed-stake and recovery simulation fields.

`SystemModelState` remains only for schema compatibility and does not classify
canonical executions. Account virtual protection is authoritative and remains
in `AccountRiskState` and `VirtualTrade`, keyed by managed-account identity.
Purchased canonical entries may settle provisionally from market ticks; the
earliest purchased actual contract supplies final monetary economics when it
settles. Unresolved canonical entries are the source of the dashboard's **Open
Trades** figure.

Global model statistics exclude virtual observations from trades, wins, losses, win rate, P/L, and winning/losing streaks. Therefore, for settled real model events:

```text
total_trades = wins + losses
win_rate = wins / total_trades
```

The dashboard summary and `/metrics/system-performance` both read this canonical ledger.

## Model P/L and stake simulation

Permanent Global Model Statistics use a reference base stake of **USD 0.50** and replay the same real model outcomes in parallel:

- **Without Martingale:** every real model trade uses the selected flat base stake.
- **With Martingale:** the stake is selected before the result using carried recovery debt, realized return economics where available, the proposal ratio as fallback, cent rounding, and the project's recovery rules.

When a purchased contract settles for a model signal, its `Trade.profit` and buy
price calibrate that signal's canonical `$0.50` result. The earliest valid
settlement is used once per signal, copied accounts are never summed, and the
informational app-markup amount is not deducted again. Tick/proposal economics
remain the fallback when no monetary settlement exists.

The dashboard's simulator is read-only and supports base stakes from `$0.50`
through `$1,000`. Anonymous viewers default to `$0.50`; authenticated viewers
default to their configured personal base stake. It replays ledger history for
the selected stake and never updates live execution settings or canonical
trades. A new canonical result resets a manually selected simulation to that
viewer default. Failed simulation requests display unavailable data rather than
a false zero.

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

Dashboard REST calls have a 20-second browser timeout, so a stalled secondary
request cannot leave the interface loading forever. `/me` returns persisted
account data immediately and schedules the slower Deriv balance refresh on a
bounded background executor. API responses expose `Server-Timing`, and requests
slower than `SLOW_REQUEST_MS` (1,000 ms by default) are logged as
`SLOW_REQUEST` without logging credentials.

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

The checked-in Netlify default points at `https://derivadmin.site`. If a
different frontend hostname is used, add its exact HTTPS origin to
`FRONTEND_ORIGINS`. Cross-origin cookie access requires
`CLIENT_SESSION_SAMESITE=none` (the current default); all public cookies remain
`Secure`, and mutation requests are accepted only from configured origins.

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

### Telegram hourly model update

The worker aligns Telegram updates to exact clock-hour boundaries in
`Africa/Nairobi` (EAT, UTC+3). Each update covers the completed hour and reports
only the canonical model trade count, reference `$0.50` P/L with and without
Martingale, the combined active Demo/Real trader count, and today's reference
Martingale P/L. The daily total uses Nairobi midnight, so a report ending at
`00:00 EAT` starts the new day's total at zero without deleting history.

Dashboard images are captured only after the Global Statistics snapshot reports
a successful render and all required values have replaced their loading states.
If that does not happen within 30 seconds, Telegram sends the text report instead
of publishing an incomplete screenshot. Deployments may override the defaults
with `TELEGRAM_TIMEZONE` and
`TELEGRAM_DASHBOARD_SCREENSHOT_TIMEOUT_SECONDS`; production should keep the
timezone set to `Africa/Nairobi`.

### Slow or inaccessible dashboard checklist

After every VPS update, check these in order:

1. `docker compose ... ps` — database, API, and worker must be healthy.
2. `docker compose ... logs api` — confirm every Alembic migration completed
   before Uvicorn started; a failed migration leaves the whole site unavailable.
3. `curl -i https://derivadmin.site/health/ready` — verify database and worker
   readiness through Caddy, not only from inside Docker.
4. Confirm both apex and `www` DNS records. Caddy redirects `www` to the apex so
   users do not receive a different cookie origin or an unhandled host.
5. For Netlify, confirm `DASHBOARD_API_BASE_URL`, `FRONTEND_ORIGINS`, and the
   browser Network panel. A blank/wrong API origin makes the static shell call
   nonexistent Netlify API routes.
6. Search API logs for `SLOW_REQUEST`; the response's `Server-Timing` header
   identifies server time separately from browser/network latency.
7. Check PostgreSQL CPU, connections, disk space, and query plans. Migration
   `0016` adds the canonical-ledger indexes used by period and open-trade cards.

The Global summary now loads canonical trades once per cached snapshot and
replays Today, Yesterday, Week, Month, and all-time views in memory instead of
issuing five separate ledger queries. Fresh WebSocket snapshots also prevent the
30-second reconciliation timer from requesting the same Global summary again.

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
