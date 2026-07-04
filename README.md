# The Underdog Legacy Model - Pattern 201

Father of Automation Series.

Pattern 201 is an Over-only Deriv digit strategy using the current APIs documented at
<https://developers.deriv.com/docs/>:

- Signal: the latest three completed digits match
  `[6-9], [0-2], [3-5]` (`BIN201x3`).
- Contract: `DIGITOVER`, barrier `3`, symbol `1HZ100V`.
- Base stake: `$0.35 USD`.
- Recovery sizing: dynamic recovery after consecutive losses, capped at `$3.50 USD`.
- Duration: one tick.
- HMM and Bayesian layers: shadow mode until deliberately changed.
- No session stop, drawdown stop, hourly cap, trade-count cap, open-contract cap,
  or consecutive-loss hard stop.

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

Render instructions are in [README_TEST2_DEPLOYMENT.md](README_TEST2_DEPLOYMENT.md).
Ubuntu VPS instructions are in [README_VPS_DEPLOYMENT.md](README_VPS_DEPLOYMENT.md).

Real trading remains locked unless `TRADING_MODE=real`,
`DERIV_ENVIRONMENT=real`, `ALLOW_REAL_TRADING=true`, and
`PRODUCTION_ACKNOWLEDGEMENT=I_ACKNOWLEDGE_REAL_MONEY_TRADING` are all present.
