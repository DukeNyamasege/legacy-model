# Contabo Full-VPS Setup Runbook

The production application runs entirely on the Contabo VPS.

## Target layout

```text
https://derivadmin.site
  |
  v
Caddy
  |-- /              -> frontend 127.0.0.1:8081
  |-- /api/*         -> API      127.0.0.1:8080
  |-- /oauth/*       -> API      127.0.0.1:8080
  `-- /ws/*          -> API WS   127.0.0.1:8080

Docker Compose
  |-- frontend
  |-- api
  |-- worker
  `-- PostgreSQL
```

## 1. Provision the server

Use Ubuntu with Docker Engine, the Docker Compose plugin, Git and Caddy. Keep PostgreSQL and application container ports private to Docker/loopback; expose only SSH, HTTP and HTTPS at the host firewall.

## 2. Clone the repository

```bash
cd /root
git clone https://github.com/DukeNyamasege/legacy-model.git
cd /root/legacy-model
git checkout main
git fetch origin --prune
git reset --hard origin/main
```

## 3. Configure the environment

```bash
cp .env.vps.example .env
chmod 600 .env
nano .env
```

Do not overwrite an existing production `.env` during ordinary deployments. Preserve the live PostgreSQL password, Deriv keys, encryption key, control key, realtime signing key, Telegram credentials and payment secrets.

Public production values include:

```text
PUBLIC_ORIGIN=https://derivadmin.site
DASHBOARD_FRONTEND_ORIGINS=https://derivadmin.site,https://www.derivadmin.site
DERIV_OAUTH_REDIRECT_URL=https://derivadmin.site/oauth/callback
CORS_ALLOWED_ORIGINS=https://derivadmin.site,https://www.derivadmin.site
```

Keep real-money execution disabled until Demo acceptance passes.

## 4. Deploy the complete stack

```bash
cd /root/legacy-model
chmod +x scripts/deploy_full_vps.sh scripts/install_full_vps_caddy.sh
PUBLIC_ORIGIN=https://derivadmin.site ./scripts/deploy_full_vps.sh
```

The deployment builds frontend, API and worker images before cutover, verifies PostgreSQL, creates a database backup, applies migrations, recreates services and verifies health.

## 5. Install or refresh Caddy

```bash
./scripts/install_full_vps_caddy.sh
```

Caddy terminates public HTTPS/WSS and proxies to the loopback frontend/API ports defined by the Compose stack.

## 6. DNS

Point the production application hostname to the Contabo public IPv4 address:

```text
A  derivadmin.site  -> CONTABO_PUBLIC_IPV4
```

Keep any diagnostic backend hostname only if it is intentionally configured in the repository Caddyfile.

## 7. OAuth

The registered callback is:

```text
https://derivadmin.site/oauth/callback
```

The callback reaches the VPS API directly through Caddy.

## 8. Acceptance

Verify:

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS https://derivadmin.site/backend-health

docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
```

Then confirm in the browser:

1. Landing page loads.
2. OAuth login completes.
3. Linked account and balance state load.
4. Realtime connects to `wss://derivadmin.site/ws/me/live`.
5. Builder Save works.
6. Start and explicit Stop work.
7. A Demo strategy qualifies, buys and settles end-to-end.
8. Closing the browser does not terminate VPS worker execution.
9. Automatic transport/provider/runtime failures recover without disabling an already-running account.
10. TP, SL and explicit manual Stop are the only terminal lifecycle events.

## 9. Real execution

Enable real-money execution only after Demo acceptance and the required production switches are deliberately set.

For normal future deployments, use `docs/FULL_VPS_DEPLOYMENT.md` as the authoritative procedure.
