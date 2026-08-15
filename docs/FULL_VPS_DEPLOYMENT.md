# Full VPS production deployment

This is the target production architecture for `derivadmin.site`:

```text
Internet
  |
  v
Caddy on Contabo :80/:443
  |-- /              -> frontend container 127.0.0.1:8081
  |-- /api/*         -> API container      127.0.0.1:8080 (prefix stripped)
  |-- /oauth/*       -> API container      127.0.0.1:8080 (path preserved)
  `-- /ws/*          -> API WebSocket      127.0.0.1:8080

Docker Compose
  |-- frontend (static React/dashboard bundle)
  |-- api
  |-- worker
  `-- database (named PostgreSQL volume)
```

Netlify is not in the request path after DNS cutover.

The repository keeps Nginx templates/scripts only as a fallback for a host where Caddy is intentionally absent. This Contabo project already has a Caddy backend edge, so **do not install Nginx on the same 80/443 ports while Caddy is active**.

## Safety properties

- PostgreSQL stays in the existing `test2_database` named volume.
- Every full deployment creates a PostgreSQL dump before application cutover.
- Frontend, API and worker images are built before running containers are replaced.
- Database migrations run before the API/worker/frontend cutover.
- The API and frontend are exposed only on loopback (`127.0.0.1`). Caddy is the only public entry point.
- OAuth stays at `https://derivadmin.site/oauth/callback`; the public domain does not change.
- Browser REST, OAuth and realtime traffic is same-origin. Netlify redirects/proxies are not used.
- `api.derivadmin.site` remains proxied to the API during the migration window for rollback/diagnostics.

## One-time migration

### 1. Update the repository on Contabo

```bash
ssh root@169.58.169.156
cd /root/legacy-model
git fetch origin
git checkout main
git reset --hard origin/main
git rev-parse --short HEAD
```

### 2. Update `/root/legacy-model/.env`

Do not replace existing secrets. Keep the live database password, Deriv keys, encryption key, control key and realtime signing key.

Make sure these production values are present:

```dotenv
PUBLIC_ORIGIN=https://derivadmin.site
FRONTEND_HOSTING_MODE=vps
DASHBOARD_FRONTEND_ORIGINS=https://derivadmin.site,https://www.derivadmin.site
DERIV_OAUTH_REDIRECT_URL=https://derivadmin.site/oauth/callback
TRUSTED_HOSTS=derivadmin.site,www.derivadmin.site,127.0.0.1,localhost,api
CORS_ALLOWED_ORIGINS=https://derivadmin.site,https://www.derivadmin.site
SESSION_COOKIE_SECURE=true
CLIENT_SESSION_SAMESITE=lax
```

Do not paste or regenerate production secrets during this migration unless a secret itself is compromised.

### 3. Build and start the complete application before changing DNS

```bash
chmod +x scripts/deploy_full_vps.sh scripts/install_full_vps_caddy.sh
PUBLIC_ORIGIN=https://derivadmin.site ./scripts/deploy_full_vps.sh
```

Expected local checks:

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8081/healthz

docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
```

The frontend, API, worker and database should all be running before DNS moves.

### 4. Install the new Caddy edge configuration

```bash
./scripts/install_full_vps_caddy.sh
```

The script:

- verifies the local API and frontend first;
- backs up the current `/etc/caddy/Caddyfile` under `deploy-backups/`;
- installs the repository full-VPS Caddyfile;
- validates the Caddy configuration;
- reloads Caddy without destroying Docker services.

The Caddyfile keeps `api.derivadmin.site` operational while adding the same-origin `derivadmin.site` frontend/API/WebSocket routes.

### 5. Point DNS to Contabo

At the DNS provider, replace the Netlify production record for the application with:

```text
A     derivadmin.site       169.58.169.156
```

Keep the existing backend record during migration:

```text
A     api.derivadmin.site   169.58.169.156
```

If you want `www.derivadmin.site`, add its A/CNAME separately and extend the Caddyfile before using it as a public hostname.

Remove conflicting Netlify CNAME/A/ALIAS records only for `derivadmin.site`. Do not remove unrelated mail/TXT records.

Wait until `derivadmin.site` resolves publicly to `169.58.169.156`. Caddy will automatically request and renew the HTTPS certificate once DNS reaches this VPS.

### 6. Production acceptance

Verify in this order:

1. `https://derivadmin.site/` loads the landing page.
2. `https://derivadmin.site/backend-health` succeeds.
3. Login redirects to Deriv and returns to `https://derivadmin.site/oauth/callback` successfully.
4. Dashboard balance/account state appears.
5. Browser realtime transport connects to `wss://derivadmin.site/ws/me/live`.
6. Save Builder works.
7. Start/Stop Auto Trading works.
8. Perform Demo trading acceptance before enabling any Real execution.
9. Clear Trades, Virtual Hook, recovery and KPI behavior remain correct.

Useful server checks:

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps

docker compose -f docker-compose.yml -f docker-compose.vps.yml \
  logs --tail=200 api worker frontend

journalctl -u caddy --since '10 minutes ago' --no-pager
curl -fsS https://derivadmin.site/backend-health
```

### 7. Retire Netlify only after acceptance

Keep the existing Netlify project untouched during the short cutover window so DNS rollback remains possible. Once the full VPS deployment is accepted, stop automatic Netlify builds and then disable/remove the Netlify production project when you no longer need rollback.

## Future releases

After the one-time Caddy/DNS setup, normal production deployment becomes:

```bash
ssh root@169.58.169.156
cd /root/legacy-model

git fetch origin
git checkout main
git reset --hard origin/main

PUBLIC_ORIGIN=https://derivadmin.site ./scripts/deploy_full_vps.sh
```

No Netlify deployment is required.

## Nginx fallback

Only use the Nginx scripts if Caddy is intentionally removed from the host. Do not run both public edges on ports 80/443 at the same time.

Fallback scripts:

```bash
./scripts/prepare_full_vps_host.sh
CERTBOT_EMAIL=YOUR_REAL_EMAIL ./scripts/enable_full_vps_https.sh
```

## Rollback

If the VPS frontend cutover fails but the previous Netlify deployment is still available:

1. Do not destroy the PostgreSQL volume.
2. Restore the previous application commit on the VPS if the failure is backend-related.
3. Restore the backed-up Caddyfile if the edge configuration is the failure.
4. For a frontend-only failure, temporarily point `derivadmin.site` DNS back to the previous Netlify record.
5. Investigate before retrying the cutover.

Database/Caddy backups created during migration are stored under:

```text
/root/legacy-model/deploy-backups/
```

Never use `docker compose down -v` in production; `-v` would remove named volumes.
