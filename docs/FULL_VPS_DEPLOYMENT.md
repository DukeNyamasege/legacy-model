# Full VPS production deployment

This repository deploys `derivadmin.site` entirely on the VPS.

```text
Internet
  |
  v
Caddy on :80/:443
  |-- /              -> frontend 127.0.0.1:8081
  |-- /api/*         -> API      127.0.0.1:8080 (prefix stripped)
  |-- /oauth/*       -> API      127.0.0.1:8080
  `-- /ws/*          -> API WS   127.0.0.1:8080

Docker Compose
  |-- frontend
  |-- api
  |-- worker
  `-- database (named PostgreSQL volume)
```

## Safety properties

- PostgreSQL remains in the existing `test2_database` named volume.
- Every full deployment creates a PostgreSQL dump before application cutover.
- Frontend, API and worker candidate images are built before live services are replaced.
- Alembic migrations run before API/worker/frontend cutover.
- API and frontend bind only to loopback; Caddy is the public HTTPS edge.
- OAuth remains at `https://derivadmin.site/oauth/callback`.
- REST, OAuth and realtime browser traffic are same-origin.
- `api.derivadmin.site` remains available for diagnostics while configured in Caddy.
- Never run `docker compose down -v` in production.

## Environment

Create the live environment from `.env.vps.example` only when bootstrapping a new server. Do not overwrite an existing production `.env` or regenerate live secrets during a normal update.

Required public values include:

```dotenv
PUBLIC_ORIGIN=https://derivadmin.site
DASHBOARD_FRONTEND_ORIGINS=https://derivadmin.site,https://www.derivadmin.site
DERIV_OAUTH_REDIRECT_URL=https://derivadmin.site/oauth/callback
TRUSTED_HOSTS=derivadmin.site,www.derivadmin.site,127.0.0.1,localhost,api
CORS_ALLOWED_ORIGINS=https://derivadmin.site,https://www.derivadmin.site
SESSION_COOKIE_SECURE=true
CLIENT_SESSION_SAMESITE=lax
```

Keep the existing values for database, Deriv, encryption, control, realtime-signing, Telegram and payment secrets.

## Normal deployment

```bash
cd /root/legacy-model
git fetch origin --prune
git checkout main
git reset --hard origin/main
chmod +x scripts/deploy_full_vps.sh
PUBLIC_ORIGIN=https://derivadmin.site ./scripts/deploy_full_vps.sh
```

For a feature branch, replace `main` with the exact authorized branch on both `git checkout` and `git reset --hard origin/...`.

The deployment validates source and Compose configuration, builds candidate images, verifies PostgreSQL, creates a database backup, applies migrations, recreates the API, worker and frontend, validates health and reloads Caddy safely.

## Caddy

The authoritative edge configuration is the repository `Caddyfile`.

Local targets are:

```text
frontend  127.0.0.1:8081
api       127.0.0.1:8080
```

Install/reload it with the provided Full-VPS Caddy script when the edge configuration itself changes. Do not run another public reverse proxy on the same ports while Caddy is active.

## Acceptance checks

Run after deployment:

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS https://derivadmin.site/backend-health

docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
```

Then verify in the application:

1. Landing page loads from `https://derivadmin.site/`.
2. OAuth login returns to `https://derivadmin.site/oauth/callback`.
3. Account and balance state load.
4. Realtime connects to `wss://derivadmin.site/ws/me/live`.
5. Builder Save works.
6. Start and explicit Stop work.
7. Demo trade proposal, BUY and settlement complete end-to-end.
8. Browser close/reopen does not terminate VPS worker execution.
9. Clear Trades remains history-only.
10. TP, SL and manual Stop are the only terminal lifecycle events.

## Fresh log verification

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml \
  logs --since=10m api worker frontend

journalctl -u caddy --since '10 minutes ago' --no-pager
```

A healthy execution cycle should contain qualification, proposal, confirmed BUY and settlement events without fatal API/worker tracebacks.

Temporary provider/network/database failures may still occur externally; they must recover without automatically disabling a running account.

## Rollback

If a release fails:

1. Keep the PostgreSQL named volume intact.
2. Restore the previous known-good Git commit.
3. Re-run the Full-VPS deployment.
4. Restore the backed-up Caddy configuration only when the edge change caused the failure.
5. Never use `docker compose down -v`.

Deployment backups are stored under:

```text
/root/legacy-model/deploy-backups/
```
