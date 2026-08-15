#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DOMAIN=${DOMAIN:-derivadmin.site}
WWW_DOMAIN=${WWW_DOMAIN:-www.${DOMAIN}}
CERTBOT_EMAIL=${CERTBOT_EMAIL:-}
SITE_FILE="/etc/nginx/sites-available/${DOMAIN}"
ENABLED_FILE="/etc/nginx/sites-enabled/${DOMAIN}"
TEMPLATE="$PROJECT_DIR/deploy/nginx/derivadmin.site.https.conf.template"

fail() {
  echo "HTTPS CUTOVER FAILED: $1" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "Run this script as root."
[ -n "$CERTBOT_EMAIL" ] || fail "Set CERTBOT_EMAIL before running this script."
[ -f "$TEMPLATE" ] || fail "Missing HTTPS Nginx template: $TEMPLATE"
command -v certbot >/dev/null 2>&1 || fail "Certbot is not installed. Run prepare_full_vps_host.sh first."

printf '%s\n' "============================================================"
printf '%s\n' "FULL VPS HTTPS CUTOVER"
printf '%s\n' "============================================================"
printf 'Domain : %s\n' "$DOMAIN"
printf 'WWW    : %s\n' "$WWW_DOMAIN"

printf '%s\n' "1. Verify local application services"
curl -fsS --max-time 5 http://127.0.0.1:8080/health >/dev/null \
  || fail "API is not healthy on 127.0.0.1:8080. Deploy the full VPS stack first."
curl -fsS --max-time 5 http://127.0.0.1:8081/healthz >/dev/null \
  || fail "Frontend is not healthy on 127.0.0.1:8081. Deploy the full VPS stack first."

printf '%s\n' "2. Request Let's Encrypt certificate"
certbot certonly \
  --webroot \
  --webroot-path /var/www/certbot \
  --domain "$DOMAIN" \
  --domain "$WWW_DOMAIN" \
  --email "$CERTBOT_EMAIL" \
  --agree-tos \
  --no-eff-email \
  --non-interactive

printf '%s\n' "3. Install HTTPS production reverse proxy"
sed \
  -e "s/__DOMAIN__/${DOMAIN}/g" \
  -e "s/__WWW_DOMAIN__/${WWW_DOMAIN}/g" \
  "$TEMPLATE" > "$SITE_FILE"
ln -sfn "$SITE_FILE" "$ENABLED_FILE"

printf '%s\n' "4. Validate and reload Nginx"
nginx -t
systemctl reload nginx

printf '%s\n' "5. Verify public HTTPS endpoints"
curl -fsS --max-time 15 "https://${DOMAIN}/" >/dev/null \
  || fail "Public frontend HTTPS check failed."
curl -fsS --max-time 15 "https://${DOMAIN}/backend-health" >/dev/null \
  || fail "Public backend HTTPS check failed."

printf '%s\n' "6. Verify certificate renewal configuration"
certbot renew --dry-run

printf '%s\n' "============================================================"
printf '%s\n' "HTTPS CUTOVER PASSED"
printf 'Frontend : https://%s/\n' "$DOMAIN"
printf 'API      : https://%s/api/*\n' "$DOMAIN"
printf 'OAuth    : https://%s/oauth/*\n' "$DOMAIN"
printf 'Realtime : wss://%s/ws/me/live\n' "$DOMAIN"
printf '%s\n' "============================================================"
