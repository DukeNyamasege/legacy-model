# Netlify Mirror Deployment

This deployment model keeps the trading bot on your local PC and publishes only
results to a public Netlify URL.

The production layout is:

- Your **local PC** runs the worker and keeps the Deriv tokens.
- **Netlify** serves the public dashboard and Netlify Functions.
- **Netlify Blobs** stores the latest mirrored dashboard state.

This means:

- Tokens stay local unless you deliberately choose a different model.
- The public dashboard is **read-only**.
- Closing the Netlify dashboard does not affect the bot.

## What Gets Synced

The local bot publishes:

- bot status
- account mode (`demo` or `real`)
- total traders
- wins, losses, total trades
- win rate
- longest win streak
- longest loss streak
- account balances
- trades per account
- recent trades

The strategy pattern itself is not shown on the public dashboard.

## Deploy To Netlify

1. Push this repository to GitHub.
2. In Netlify, choose **Add new site > Import an existing project**.
3. Select this repository.
4. Netlify reads `netlify.toml`.
5. Do not override:
   - build command
   - publish directory
   - functions directory
6. Add this environment variable in Netlify:

```text
NETLIFY_SYNC_TOKEN=choose-a-long-random-secret
```

7. Deploy the site.

After deployment, Netlify gives you a URL like:

```text
https://your-site.netlify.app
```

Your local bot will post results to this Netlify function endpoint:

```text
https://your-site.netlify.app/.netlify/functions/sync-ingest
```

## Run The Bot Locally And Sync To Netlify

On the PC running the bot, set these environment variables before startup:

```powershell
$env:NETLIFY_SYNC_URL="https://your-site.netlify.app/.netlify/functions/sync-ingest"
$env:NETLIFY_SYNC_TOKEN="choose-a-long-random-secret"
$env:NETLIFY_SYNC_INTERVAL_SECONDS="15"
```

Then start the local dashboard/worker:

```powershell
& ".\.venv\Scripts\python.exe" local_dashboard.py
```

The worker will push fresh dashboard snapshots to Netlify every few seconds.

## Security Notes

- Keep **Deriv tokens local** on your PC.
- Do **not** put live trading tokens into Netlify environment variables unless
  you intentionally want cloud-side token storage.
- The Netlify site only needs `NETLIFY_SYNC_TOKEN`.
- Use a long random sync token and rotate it if exposed.

## Public Dashboard Behavior

The Netlify dashboard is automatically read-only:

- no start/stop control
- no emergency stop
- no account token editing
- no strategy signal display

It mirrors your local bot results only.

## Optional Local + Public Split

You can keep using:

- local dashboard at `http://127.0.0.1:8080` for control
- Netlify URL for public/remote monitoring

That is the recommended setup.

## Official References

- Netlify Functions:
  https://docs.netlify.com/functions/overview/
- Netlify Blobs:
  https://docs.netlify.com/blobs/overview/
- Netlify environment variables:
  https://docs.netlify.com/build/configure-builds/environment-variables/
