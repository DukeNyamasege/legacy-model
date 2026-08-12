# Contabo Backend Setup Runbook

This is the production backend runbook for the Netlify + Contabo architecture.

## Target layout

```text
https://derivadmin.site      -> Netlify frontend
https://api.derivadmin.site  -> Contabo / Caddy / FastAPI
wss://api.derivadmin.site    -> Contabo realtime WebSocket
```

Contabo runs only PostgreSQL, FastAPI and the Custom Strategy worker. It does not serve the browser frontend.

## 1. Provision the server

Use a clean Ubuntu image and NVMe storage. Do not install Railway, Render, Replit or another PaaS layer on this server.

Keep financial execution disabled during infrastructure setup.

## 2. Initial server packages

SSH as root, update the server, install Git, Docker Engine, the Docker Compose plugin and Caddy, then confirm:

```bash
docker --version
docker compose version
caddy version
git --version
```

Open inbound ports 22, 80 and 443 in the Contabo firewall/security policy. PostgreSQL and FastAPI remain private to Docker/loopback.

## 3. Clone production main

```bash
cd /root
git clone https://github.com/DukeNyamasege/legacy-model.git
cd /root/legacy-model
git checkout main
git fetch origin
git pull --ff-only origin main
git rev-parse HEAD
```

## 4. Create backend environment

```bash
cp .env.vps.example .env
chmod 600 .env
nano .env
```

Replace all placeholders. Keep these disabled during first deployment:

```text
DERIV_TRADING_ENABLED=false
ALLOW_REAL_TRADING=false
```

Set the exact Netlify site URL and production frontend domain in `DASHBOARD_FRONTEND_ORIGINS` and `CORS_ALLOWED_ORIGINS`.

## 5. DNS

Create an A record:

```text
api.derivadmin.site -> CONTABO_PUBLIC_IPV4
```

Wait until the hostname resolves to the Contabo server before starting Caddy certificate provisioning.

## 6. Caddy

```bash
cp /root/legacy-model/Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl restart caddy
systemctl status caddy --no-pager
```

Caddy terminates HTTPS/WSS and forwards only to FastAPI on `127.0.0.1:8080`.

## 7. Deploy backend

```bash
cd /root/legacy-model
sh -n scripts/deploy_dedicated_backend.sh
sh scripts/deploy_dedicated_backend.sh
```

Verify:

```bash
docker compose -f docker-compose.yml ps
curl -i http://127.0.0.1:8080/health
curl -i https://api.derivadmin.site/health
```

## 8. Connect Netlify

In the Netlify production environment set:

```text
BACKEND_ORIGIN=https://api.derivadmin.site
DASHBOARD_WS_BASE_URL=wss://api.derivadmin.site
```

Redeploy `main`. The build emits `/api/*` and `/oauth/*` proxy rewrites and configures the direct WSS client.

## 9. OAuth

Register/use this frontend callback:

```text
https://derivadmin.site/oauth/callback
```

The callback reaches Contabo through the Netlify `/oauth/*` proxy.

## 10. Demo acceptance

After HTTPS, OAuth and realtime WSS work, set:

```text
DERIV_TRADING_ENABLED=true
TRADING_MODE=demo
ALLOW_REAL_TRADING=false
```

Redeploy/restart the backend and test one Demo account and one market. Required execution markers:

```text
CUSTOM_STRATEGY_SIGNAL_QUALIFIED
PURCHASE_EXECUTION_REQUEST
PURCHASE_CONFIRMED
CONTRACT_SETTLED
```

Also test closing the browser while Auto Trading is enabled. The worker must continue trading and the reopened Netlify frontend must reconstruct current state from the backend.

## 11. Real execution

Do not enable real-money execution until all Demo acceptance gates pass. Real mode requires the explicit production switches already documented in `.env.vps.example` and runtime configuration.
