# The Underdog Legacy Model - Rising Over 2

Father of Automation Series.

The bot is an Over-2 Deriv digit strategy using the current APIs documented at
<https://developers.deriv.com/docs/>:

- Signal: the latest five completed digit bins match
  `[6-9], [6-9], [0-2], [0-2], [3-5]` (`BIN22001x5`) and the latest
  three quotes are strictly rising.
- Contract: `DIGITOVER`, barrier `2`, symbol `1HZ100V`.
- Base stake: `$0.50 USD`.
- Recovery sizing: configured two-run recovery, capped by `maximum_stake`.
- Duration: one tick.
- Bayesian layer: active gate using a locked historical calibration and the
  markup-adjusted break-even payout. HMM remains observation-only.
- No session stop, drawdown stop, hourly cap, trade-count cap, open-contract cap,
  or consecutive-loss hard stop.

The locked 60,000/40,000 chronological research split produced `51/58` wins in
development and `56/67` on the untouched holdout. This is a small historical
sample, not a profit guarantee; live outcomes update the Bayesian gate once per
copy-trade signal rather than once per copied account.

Deriv app markup is configured on the Registered App, not in proposal or
bulk-purchase request bodies. `DERIV_APP_MARKUP_PERCENTAGE` is only the expected
rate used for conservative calculations and verification. The worker records
settled `app_markup_amount`, and administrators can compare it with Deriv using
`GET /control/markup-statistics`.

## Local Run

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".\.venv\Scripts\python.exe" local_dashboard.py
```

Open `http://127.0.0.1:8080`. The terminal prints a temporary control key; enter
it in the dashboard to use Start/Stop. Start resumes candidate purchases and Stop
places the worker in manual pause while ticks, settlements, database heartbeats,
and recovery continue.

The local launcher runs the API and worker as separate processes while keeping
worker logs visible. It defaults to demo trading and SQLite at
`data/bin22001.db`.
Use PostgreSQL through `DATABASE_URL` for deployment.

## Test And Verify

```powershell
& ".\.venv\Scripts\python.exe" -m unittest -v
& ".\.venv\Scripts\python.exe" full_verify.py
& ".\.venv\Scripts\python.exe" -m scripts.export_test2
```

The one-time reset command archives active historical files before creating a
zeroed Test 2 run:

```powershell
$env:TEST1_CONTRACTS_RECONCILED = "true"
& ".\.venv\Scripts\python.exe" -m scripts.reset_test_data --target test2 --confirm RESET_TEST2
```

Never set the reconciliation flag until every legacy open-contract ID has been
checked through Deriv.

## Deployment

Run the dashboard, API, and worker together on an Ubuntu VPS behind Caddy.
Instructions are in [README_VPS_DEPLOYMENT.md](README_VPS_DEPLOYMENT.md).

Real trading remains locked unless `TRADING_MODE=real`,
`DERIV_ENVIRONMENT=real`, `ALLOW_REAL_TRADING=true`, and
`PRODUCTION_ACKNOWLEDGEMENT=I_ACKNOWLEDGE_REAL_MONEY_TRADING` are all present.
