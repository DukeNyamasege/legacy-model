#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DOMAIN=${DOMAIN:-derivadmin.site}
WWW_DOMAIN=${WWW_DOMAIN:-www.${DOMAIN}}
SITE_FILE="/etc/nginx/sites-available/${DOMAIN}"
ENABLED_FILE="/etc/nginx/sites-enabled/${DOMAIN}"
TEMPLATE="$PROJECT_DIR/deploy/nginx/derivadmin.site.http.conf.template"

fail() {
  echo "VPS HOST PREPARATION FAILED: $1" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "Run this script as root."
[ -f "$TEMPLATE" ] || fail "Missing Nginx HTTP template: $TEMPLATE"
command -v apt-get >/dev/null 2>&1 || fail "This bootstrap currently supports Debian/Ubuntu apt hosts."

printf '%s\n' "============================================================"
printf '%s\n' "FULL VPS HOST PREPARATION"
printf '%s\n' "============================================================"
printf 'Domain  : %s\n' "$DOMAIN"
printf 'WWW     : %s\n' "$WWW_DOMAIN"
printf 'Project : %s\n' "$PROJECT_DIR"

printf '%s\n' "1. Install Nginx and Certbot"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot

printf '%s\n' "2. Prepare ACME webroot"
mkdir -p /var/www/certbot
chmod 755 /var/www/certbot

printf '%s\n' "3. Install temporary HTTP reverse-proxy site"
sed \
  -e "s/__DOMAIN__/${DOMAIN}/g" \
  -e "s/__WWW_DOMAIN__/${WWW_DOMAIN}/g" \
  "$TEMPLATE" > "$SITE_FILE"
ln -sfn "$SITE_FILE" "$ENABLED_FILE"
rm -f /etc/nginx/sites-enabled/default

printf '%s\n' "4. Validate and reload Nginx"
nginx -t
systemctl enable --now nginx
systemctl reload nginx

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
  printf '%s\n' "5. UFW is active; allow HTTP/HTTPS through Nginx"
  ufw allow 'Nginx Full'
else
  printf '%s\n' "5. Firewall unchanged (UFW not active)"
fi

printf '%s\n' "============================================================"
printf '%s\n' "HOST PREPARATION PASSED"
printf '%s\n' "Next DNS records:"
printf '  A  %s      -> 169.58.169.156\n' "$DOMAIN"
printf '  A  %s -> 169.58.169.156\n' "$WWW_DOMAIN"
printf '%s\n' "After DNS points to this VPS, run:"
printf '  CERTBOT_EMAIL=you@example.com ./scripts/enable_full_vps_https.sh\n'
printf '%s\n' "============================================================"
