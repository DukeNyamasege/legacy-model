# Netlify Frontend Deployment

The frontend dashboard is hosted on Netlify. The backend, trading bot, APIs,
sessions, and real-time services run continuously on the Ubuntu VPS.

## Architecture

- **Netlify** serves the static dashboard HTML and proxies OAuth start/callback
  to the VPS backend.
- **VPS** runs the FastAPI API and the trading worker. The frontend connects
  directly to the VPS via WebSocket (`/ws/dashboard`) for real-time updates and
  via HTTP for REST calls (`/metrics/summary`, `/me`, `/me/auto-trade`, etc.).
- **Netlify Functions** are only used for:
  - `oauth-start`: redirects to the VPS OAuth start endpoint
  - `oauth-callback`: forwards the OAuth callback to the VPS so session cookies
    are set on the VPS origin
  - `sync-ingest`: receives periodic HTTP pushes from the bot (legacy fallback)

## Deploy To Netlify

1. Push this repository to GitHub.
2. In Netlify, choose **Add new site > Import an existing project**.
3. Select this repository.
4. Netlify reads `netlify.toml` automatically.
5. Add this environment variable in Netlify:

```text
API_BASE_URL=https://your-vps-domain.com
```

Replace `your-vps-domain.com` with your actual VPS domain (with HTTPS via a
reverse proxy such as Caddy or Nginx).

6. Deploy the site.

The build script (`scripts/build-netlify.mjs`) injects the `API_BASE_URL` into
the dashboard HTML so the frontend connects directly to the VPS for both
WebSocket and REST calls.

## OAuth Flow

1. User clicks "Login with Deriv" on the Netlify dashboard.
2. Netlify Function `oauth-start` redirects to the VPS `/oauth/start`.
3. VPS redirects to Deriv's OAuth sign-in page.
4. After approval, Deriv redirects to `oauth_redirect_url` (configured as
   `https://your-vps-domain.com/oauth/callback`).
5. VPS exchanges the code for tokens, stores the session, and sets a session
   cookie on the VPS domain.
6. User is redirected back to the Netlify dashboard, which now connects to the
   VPS via WebSocket for real-time updates.

## Required Environment Variables (Netlify)

- `API_BASE_URL`: the HTTPS URL of your VPS backend (e.g. `https://derivadmin.site`)

## Required Environment Variables (VPS)

See [README_VPS_DEPLOYMENT.md](README_VPS_DEPLOYMENT.md) for the full VPS setup.

Key OAuth-related variables:

- `DERIV_OAUTH_CLIENT_ID`: your Deriv app ID
- `DERIV_OAUTH_REDIRECT_URL`: `https://your-vps-domain.com/oauth/callback`
- `DERIV_TOKEN_ENCRYPTION_KEY`: Fernet key for encrypting stored tokens
- `FRONTEND_ORIGINS`: your Netlify domain(s) for CORS
