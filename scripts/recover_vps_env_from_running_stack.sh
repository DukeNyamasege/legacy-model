#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_DIR"

TARGET="$PROJECT_DIR/.env"
EXAMPLE="$PROJECT_DIR/.env.vps.example"
RAW=$(mktemp)
KEYS=$(mktemp)
OUTPUT=$(mktemp)
trap 'rm -f "$RAW" "$KEYS" "$OUTPUT"' EXIT INT TERM
chmod 600 "$RAW" "$OUTPUT"

if [ -s "$TARGET" ]; then
  echo "Existing .env is present; recovery is not needed."
  exit 0
fi
if [ ! -f "$EXAMPLE" ]; then
  echo "Cannot recover .env: .env.vps.example is missing." >&2
  exit 1
fi

find_container() {
  service=$1
  id=$(docker ps -q \
    --filter "label=com.docker.compose.project=legacy-model" \
    --filter "label=com.docker.compose.service=$service" \
    | head -n 1)
  if [ -z "$id" ]; then
    id=$(docker ps -q --filter "name=legacy-model-${service}-1" | head -n 1)
  fi
  printf '%s' "$id"
}

API_ID=$(find_container api)
WORKER_ID=$(find_container worker)
DATABASE_ID=$(find_container database)

if [ -z "$API_ID" ] || [ -z "$DATABASE_ID" ]; then
  echo "Cannot recover .env: running legacy-model API/database containers were not found." >&2
  echo "Do not recreate or remove the current containers; inspect docker ps first." >&2
  exit 1
fi

for id in "$API_ID" "$WORKER_ID" "$DATABASE_ID"; do
  [ -n "$id" ] || continue
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$id" >> "$RAW"
done

# Recover only variables that are intentionally part of the VPS example or are
# explicitly referenced by Compose. Never copy container internals such as PATH,
# HOSTNAME or a generated DATABASE_URL into the durable .env file.
{
  sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' "$EXAMPLE"
  grep -ho '\${[A-Z][A-Z0-9_]*' docker-compose.yml docker-compose.vps.yml 2>/dev/null \
    | sed 's/^${//' || true
} | sort -u > "$KEYS"

python3 - "$RAW" "$KEYS" "$OUTPUT" <<'PY'
import json
import sys
from pathlib import Path

raw_path, keys_path, output_path = map(Path, sys.argv[1:4])
values = {}
for line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    values.setdefault(key, value)

keys = [line.strip() for line in keys_path.read_text().splitlines() if line.strip()]
with output_path.open("w", encoding="utf-8") as handle:
    handle.write("# Recovered from the currently running production containers.\n")
    handle.write("# Values are intentionally not printed by the recovery script.\n")
    for key in keys:
        if key not in values:
            continue
        handle.write(f"{key}={json.dumps(values[key])}\n")
PY

required="POSTGRES_PASSWORD DERIV_APP_ID DERIV_OAUTH_CLIENT_ID DERIV_OAUTH_REDIRECT_URL DERIV_TOKEN_ENCRYPTION_KEY CONTROL_API_KEY"
missing=""
for key in $required; do
  if ! grep -q "^${key}=" "$OUTPUT"; then
    missing="$missing $key"
  fi
done
if [ -n "$missing" ]; then
  echo "Recovery stopped: running containers did not expose these required variable names:$missing" >&2
  echo "No .env file was written. Existing containers were not changed." >&2
  exit 1
fi

mv "$OUTPUT" "$TARGET"
chmod 600 "$TARGET"

if ! docker compose -f docker-compose.yml -f docker-compose.vps.yml config --quiet >/dev/null 2>&1; then
  echo "Recovered .env was written with mode 600, but Compose validation still failed." >&2
  echo "Existing production containers were not changed. Review variable names only; do not print secret values." >&2
  exit 1
fi

echo "VPS environment recovered safely from the currently running stack."
echo "File: /root/legacy-model/.env"
echo "Mode: $(stat -c '%a' "$TARGET" 2>/dev/null || printf '600')"
echo "Compose validation: OK"
echo "No container, volume, database, credential, or trade record was modified."
