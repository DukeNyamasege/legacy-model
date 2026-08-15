# Full VPS production deployment

This is the target production architecture for `derivadmin.site`:

```text
Internet
  |
  v
Nginx on Contabo :80/:443
  |-- /              -> frontend container 127.0.0.1:8081
  |-- /api/*         -> API container      127.0.0.1:8080 (prefix stripped)
  |-- /oauth/*       -> API container      127.0.0.1:8080 (path preserved)
  `-- /ws/*          -> API WebSocket      127.0.0.1:8080 (Upgrade preserved)

Docker Compose
  |-- frontend (static React/dashboard bundle)
  |-- api
  |-- worker
  `-- database (named PostgreSQL volume)
```

Netlify is not in the request path after DNS cutover.

## Safety properties

- PostgreSQL stays in the existing `test2_database` named volume.
- Every full deployment creates a PostgreSQL dump before application cutover.
- Frontend, API and worker images are built before running containers are replaced.
- Database migrations run before the API/worker/frontend cutover.
- The API and frontend are exposed only on loopback (`127.0.0.1`). Nginx is the only public entry point.
- OAuth stays at `https://derivadmin.site/oauth/callback`; the public domain does not change.
- Browser REST, OAuth and realtime traffic is same-origin. Netlify redirects/proxies are not used.

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
chmod +x scripts/deploy_full_vps.sh \
  scripts/prepare_full_vps_host.sh \
  scripts/enable_full_vps_https.sh

PUBLIC_ORIGIN=https://derivadmin.site ./scripts/deploy_full_vps.sh
```

Expected local checks:

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8081/healthz

docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
```

The frontend, API, worker and database should all be running before DNS moves.

### 4. Prepare host Nginx

```bash
./scripts/prepare_full_vps_host.sh
```

This installs Nginx/Certbot when required and installs the temporary HTTP reverse proxy used for ACME validation.

Before DNS cutover, you can verify the host proxy locally:

```bash
curl -I -H 'Host: derivadmin.site' http://127.0.0.1/
curl -fsS -H 'Host: derivadmin.site' http://127.0.0.1/backend-health
```

### 5. Point DNS to Contabo

At the DNS provider, replace the Netlify production records for the application with:

```text
A     derivadmin.site       169.58.169.156
A     www.derivadmin.site   169.58.169.156
```

Remove conflicting Netlify CNAME/A/ALIAS records for these exact hostnames. Do not remove unrelated mail/TXT records.

Wait until both names resolve publicly to `169.58.169.156`.

### 6. Enable HTTPS

```bash
CERTBOT_EMAIL=YOUR_REAL_EMAIL ./scripts/enable_full_vps_https.sh
```

The script verifies the local API/frontend first, requests the certificate, installs the HTTPS Nginx config, reloads Nginx, verifies the public frontend/backend and checks certificate renewal.

### 7. Production acceptance

Verify in this order:

1. `https://derivadmin.site/` loads the landing page.
2. Login redirects to Deriv and returns to `https://derivadmin.site/oauth/callback` successfully.
3. Dashboard balance/account state appears.
4. Browser realtime transport connects to `wss://derivadmin.site/ws/me/live`.
5. Save Builder works.
6. Start/Stop Auto Trading works.
7. Perform Demo trading acceptance before enabling any Real execution.
8. Clear Trades, Virtual Hook, recovery and KPI behavior remain correct.

Useful server checks:

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps

docker compose -f docker-compose.yml -f docker-compose.vps.yml \
  logs --tail=200 api worker frontend

curl -fsS https://derivadmin.site/backend-health
```

### 8. Retire Netlify only after acceptance

Keep the existing Netlify project untouched during the cutover window so DNS rollback remains possible. Once the full VPS deployment is accepted, stop automatic Netlify builds and then disable/remove the Netlify production project when you no longer need rollback.

## Future releases

After the one-time host/DNS/TLS setup, normal production deployment becomes:

```bash
ssh root@169.58.169.156
cd /root/legacy-model

git fetch origin
git checkout main
git reset --hard origin/main

PUBLIC_ORIGIN=https://derivadmin.site ./scripts/deploy_full_vps.sh
```

No Netlify deployment is required.

## Rollback

If the VPS frontend cutover fails but the previous Netlify deployment is still available:

1. Do not destroy the PostgreSQL volume.
2. Restore the previous application commit on the VPS if the failure is backend-related.
3. For a frontend/edge failure, temporarily point the application DNS back to the previous Netlify records.
4. Investigate before retrying the cutover.

Database backups created by the deployment script are stored under:

```text
/root/legacy-model/deploy-backups/
```

Never use `docker compose down -v` in production; `-v` would remove named volumes.
