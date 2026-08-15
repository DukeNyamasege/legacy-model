#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SOURCE="$PROJECT_DIR/Caddyfile"
TARGET=/etc/caddy/Caddyfile
BACKUP_DIR="$PROJECT_DIR/deploy-backups"

fail() {
  echo "CADDY CUTOVER FAILED: $1" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "Run this script as root."
[ -f "$SOURCE" ] || fail "Missing repository Caddyfile."
command -v caddy >/dev/null 2>&1 || fail "Caddy is not installed on this VPS. Use the Nginx fallback only if Caddy is intentionally absent."

curl -fsS --max-time 5 http://127.0.0.1:8080/health >/dev/null \
  || fail "API is not healthy on 127.0.0.1:8080. Deploy the full VPS stack first."
curl -fsS --max-time 5 http://127.0.0.1:8081/healthz >/dev/null \
  || fail "Frontend is not healthy on 127.0.0.1:8081. Deploy the full VPS stack first."

mkdir -p "$BACKUP_DIR"
timestamp=$(date -u +"%Y%m%dT%H%M%SZ")
if [ -f "$TARGET" ]; then
  cp "$TARGET" "$BACKUP_DIR/Caddyfile.before_full_vps_${timestamp}"
fi

cp "$SOURCE" "$TARGET"
chown root:root "$TARGET"
chmod 644 "$TARGET"

caddy validate --config "$TARGET" --adapter caddyfile
systemctl enable --now caddy
systemctl reload caddy

printf '%s\n' "============================================================"
printf '%s\n' "CADDY FULL VPS EDGE INSTALLED"
printf '%s\n' "============================================================"
printf '%s\n' "DNS required before public HTTPS can complete:"
printf '%s\n' "  derivadmin.site     -> 169.58.169.156"
printf '%s\n' "  api.derivadmin.site -> 169.58.169.156 (keep during migration)"
printf '%s\n' "Caddy will obtain/renew TLS certificates automatically after DNS resolves."
printf '%s\n' "============================================================"
