# Netlify Frontend and Render Backend

The production layout is:

- Netlify serves the static dashboard.
- Render runs one FastAPI web service.
- Render runs one background trading worker continuously.
- Render Postgres stores bot state, trades, balances, and control status.

The Netlify site does not run the bot. Closing the browser does not stop the
Render worker.

## Before deployment

1. Revoke any Deriv PAT that has been pasted into chat, logs, screenshots, or
   source files.
2. Generate a new PAT with only the scopes required by the trading worker.
3. Keep the new PAT out of Git and provide it only through Render's secret
   environment-variable prompt.
4. Push this repository to a private GitHub repository.
5. Confirm `git status` does not include `.env`, `tokens.txt`, databases, or
   other runtime files.

The committed configuration starts in demo mode. Keep it there for the first
deployment.

## Deploy the backend to Render

1. Sign in to Render and select **New > Blueprint**.
2. Connect the GitHub repository containing this project.
3. Render detects `render.yaml`.
4. During Blueprint creation, enter these prompted values:
   - `DERIV_APP_ID`: use the same Deriv application ID for the API and worker.
   - `DERIV_TOKEN`: enter the newly generated PAT only for the worker.
5. Apply the Blueprint.
6. Wait for all three resources:
   - `underdog-bin22001-db`
   - `underdog-bin22001-api`
   - `underdog-bin22001-worker`
7. Open the API service and copy its public URL, for example:
   `https://underdog-bin22001-api.onrender.com`
8. Verify:
   - `<RENDER_API_URL>/health/live` returns a live status.
   - `<RENDER_API_URL>/health/ready` returns ready after the worker heartbeat.
9. Open the API service's environment page and securely copy the generated
   `CONTROL_API_KEY`. Do not add this key to Netlify or Git.

The Blueprint uses paid Starter instances for the API and worker because Render
background workers do not support the free instance type. Postgres is also a
persistent managed resource. Review the displayed Render cost before applying
the Blueprint.

## Deploy the frontend to Netlify

1. In Netlify, select **Add new site > Import an existing project**.
2. Connect the same GitHub repository.
3. Netlify reads `netlify.toml`; do not override its build command or publish
   directory.
4. Add this environment variable with the **Builds** scope:
   - `API_BASE_URL=https://your-render-api.onrender.com`
5. Trigger the production deployment.
6. Open the generated `netlify.app` URL.
7. Enter the Render `CONTROL_API_KEY` in the dashboard's **Control key** field.
   It is kept only in the current browser tab's session storage.
8. Press **Start**. The status should change to `Running`.
9. Confirm the Render worker logs show tick streaming and `BIN22001x5`
   monitoring.

Render currently accepts Netlify production and deploy-preview origins through
the configured origin regex. If a custom frontend domain is added, set the API
service environment variable below and redeploy the API:

```text
FRONTEND_ORIGINS=https://trade.example.com
```

Use comma-separated origins when more than one custom domain is required.

## Deployment environment

The initial worker values must remain:

```text
DERIV_ENVIRONMENT=demo
TRADING_MODE=demo
ALLOW_REAL_TRADING=false
DERIV_TRADING_ENABLED=true
TEST_RUN_ID=bin22001
```

Do not enable real trading until demo verification is complete. Real mode also
requires all of these explicit worker values:

```text
DERIV_ENVIRONMENT=real
TRADING_MODE=real
ALLOW_REAL_TRADING=true
PRODUCTION_ACKNOWLEDGEMENT=I_ACKNOWLEDGE_REAL_MONEY_TRADING
```

The API and worker must use the same `DATABASE_URL` and `TEST_RUN_ID`. The
Blueprint configures both automatically.

## Post-deployment checks

1. Refresh updates status, balance, model evidence, and recent trades.
2. Start, Stop, and Emergency stop work only with the control key.
3. Restart the worker and confirm database totals remain unchanged.
4. Confirm only one worker instance exists.
5. Confirm the worker reconnects to Deriv after a Render restart.
6. Keep the first deployment in demo mode and review forward results before any
   real-money decision.

Official references:

- Render Blueprint specification:
  https://render.com/docs/blueprint-spec
- Render environment variables:
  https://render.com/docs/configure-environment-variables
- Netlify file-based configuration:
  https://docs.netlify.com/build/configure-builds/file-based-configuration/
- Netlify environment variables:
  https://docs.netlify.com/build/environment-variables/overview/
