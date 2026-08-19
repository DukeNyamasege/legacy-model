#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   bash scripts/collect_account_runtime_report.sh DOT91317422
# Optional:
#   SINCE=3h bash scripts/collect_account_runtime_report.sh DOT91317422
#
# This script is read-only. It does not print .env and deliberately removes
# ManagedAccount.token_secret from database output.

ACCOUNT_HINT="${1:-DOT91317422}"
SINCE="${SINCE:-3h}"

if [[ ! "$ACCOUNT_HINT" =~ ^[A-Za-z0-9_.*-]+$ ]]; then
  echo "Invalid account hint: use only letters, digits, _, ., *, or -" >&2
  exit 2
fi

SUFFIX="${ACCOUNT_HINT: -3}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="vps-runtime-report-${SUFFIX}-${STAMP}.txt"

if [[ -f docker-compose.vps.yml ]]; then
  COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.vps.yml)
else
  COMPOSE=(docker compose -f docker-compose.yml)
fi

section() {
  printf '\n\n==================== %s ====================\n' "$1"
}

psql_query() {
  local sql="$1"
  "${COMPOSE[@]}" exec -T database sh -lc \
    'psql -X -v ON_ERROR_STOP=1 -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    <<<"$sql"
}

resolve_ids() {
  "${COMPOSE[@]}" exec -T database sh -lc \
    'psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    <<SQL
SELECT DISTINCT id
FROM managed_accounts
WHERE label ILIKE '%${SUFFIX}%'
   OR id IN (
      SELECT managed_account_id FROM account_risk_states
      WHERE account_id_masked ILIKE '%${SUFFIX}%'
   )
   OR id IN (
      SELECT managed_account_id FROM trades
      WHERE account_id_masked ILIKE '%${SUFFIX}%'
   )
ORDER BY id;
SQL
}

exec > >(tee "$REPORT") 2>&1

section "REPORT METADATA"
echo "utc_now=$(date -u --iso-8601=seconds)"
echo "account_hint=$ACCOUNT_HINT"
echo "account_suffix=$SUFFIX"
echo "log_window=$SINCE"
echo "host=$(hostname)"
echo "pwd=$(pwd)"

section "GIT / DEPLOYMENT"
git status --short || true
git rev-parse HEAD || true
git log -1 --oneline || true
git branch --show-current || true

section "DOCKER SERVICES"
"${COMPOSE[@]}" ps || true

section "HEALTH"
"${COMPOSE[@]}" exec -T api python - <<'PY' || true
import time
import urllib.request
for url in (
    "http://127.0.0.1:8080/health/database",
    "http://127.0.0.1:8080/health/frontend-backend",
):
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            raw = response.read().decode("utf-8", "replace")
            elapsed = round((time.monotonic() - started) * 1000, 1)
            print(url, response.status, f"duration_ms={elapsed}", raw[:2000])
    except Exception as exc:
        elapsed = round((time.monotonic() - started) * 1000, 1)
        print(url, "ERROR", f"duration_ms={elapsed}", type(exc).__name__, str(exc))
PY

MANAGED_IDS="$(resolve_ids 2>/dev/null | tr -d '\r' | sed '/^[[:space:]]*$/d' || true)"
section "ACCOUNT RESOLUTION"
echo "managed_account_ids:"
if [[ -n "$MANAGED_IDS" ]]; then
  printf '%s\n' "$MANAGED_IDS"
else
  echo "NONE FOUND"
fi

ID_LIST="$(printf '%s\n' "$MANAGED_IDS" | sed '/^[[:space:]]*$/d' | paste -sd, -)"
ID_REGEX="$(printf '%s\n' "$MANAGED_IDS" | sed '/^[[:space:]]*$/d' | paste -sd'|' -)"
if [[ -z "$ID_LIST" ]]; then
  ID_LIST="-1"
fi
if [[ -z "$ID_REGEX" ]]; then
  ID_REGEX="__no_managed_id__"
fi

section "MANAGED ACCOUNT (TOKEN REDACTED)"
psql_query "
SELECT jsonb_pretty(to_jsonb(m) - 'token_secret')
FROM managed_accounts AS m
WHERE m.id IN (${ID_LIST})
   OR m.label ILIKE '%${SUFFIX}%'
ORDER BY m.id;
" || true

section "ACCOUNT RISK STATE"
psql_query "
SELECT jsonb_pretty(to_jsonb(r))
FROM account_risk_states AS r
WHERE r.managed_account_id IN (${ID_LIST})
   OR r.account_id_masked ILIKE '%${SUFFIX}%'
ORDER BY r.managed_account_id;
" || true

section "CUSTOM STRATEGY / HARD STOP / RECOVERY PREFERENCES"
psql_query "
SELECT preference_key, preference_value, updated_at
FROM runtime_preferences
WHERE substring(preference_key from ':([0-9]+)$')::integer IN (${ID_LIST})
ORDER BY preference_key;
" || true

section "RECENT ACTUAL TRADES"
psql_query "
SELECT jsonb_pretty(to_jsonb(t))
FROM trades AS t
WHERE t.managed_account_id IN (${ID_LIST})
   OR t.account_id_masked ILIKE '%${SUFFIX}%'
ORDER BY t.purchase_time DESC
LIMIT 100;
" || true

section "ACTUAL TRADE COUNTS / PNL"
psql_query "
SELECT
  COUNT(*) AS actual_rows,
  COUNT(*) FILTER (WHERE outcome = 'WIN') AS wins,
  COUNT(*) FILTER (WHERE outcome = 'LOSS') AS losses,
  COALESCE(SUM(buy_price), 0) AS total_buy_price,
  COALESCE(SUM(payout), 0) AS total_payout,
  COALESCE(SUM(profit), 0) AS total_profit
FROM trades
WHERE (managed_account_id IN (${ID_LIST}) OR account_id_masked ILIKE '%${SUFFIX}%')
  AND purchase_time >= now() - interval '6 hours';
" || true

section "RECENT VIRTUAL TRADES"
psql_query "
SELECT jsonb_pretty(to_jsonb(v))
FROM virtual_trades AS v
WHERE v.managed_account_id IN (${ID_LIST})
ORDER BY COALESCE((to_jsonb(v)->>'created_at')::timestamptz, now()) DESC
LIMIT 100;
" || true

section "VIRTUAL COUNTS"
psql_query "
SELECT
  managed_account_id,
  COUNT(*) AS virtual_rows,
  COUNT(*) FILTER (WHERE result = 'VIRTUAL_WIN') AS virtual_wins,
  COUNT(*) FILTER (WHERE result = 'VIRTUAL_LOSS') AS virtual_losses,
  COUNT(*) FILTER (WHERE result = 'OPEN') AS virtual_open
FROM virtual_trades
WHERE managed_account_id IN (${ID_LIST})
GROUP BY managed_account_id
ORDER BY managed_account_id;
" || true

section "RECENT ACCOUNT AUDIT EVENTS"
psql_query "
SELECT created_at, action, actor, source_ip, details
FROM audit_events
WHERE created_at >= now() - interval '6 hours'
  AND (
    details::text ILIKE '%${SUFFIX}%'
    OR action ILIKE '%STOP%'
    OR action ILIKE '%TAKE%PROFIT%'
    OR action ILIKE '%STOP%LOSS%'
    OR action ILIKE '%VIRTUAL%'
    OR action ILIKE '%PURCHASE%'
  )
ORDER BY created_at DESC
LIMIT 300;
" || true

LOG_PATTERN="${ACCOUNT_HINT}|${SUFFIX}|managed_id=(${ID_REGEX})|CUSTOM_VIRTUAL|DIRECT_WORKER|DIRECT_EXECUTION|HARD_STOP|TAKE_PROFIT|STOP_LOSS|TP_|SL_|PURCHASE|BUY|RECOVERY|SETTLED|EXECUTION_FAILED"

section "WORKER LOGS"
"${COMPOSE[@]}" logs --since "$SINCE" --no-color worker 2>&1 \
  | grep -Ei "$LOG_PATTERN" \
  | tail -n 2000 || true

section "API LOGS"
"${COMPOSE[@]}" logs --since "$SINCE" --no-color api 2>&1 \
  | grep -Ei "$LOG_PATTERN|/me/stop-trading|/me/direct-execution/stop|/me/direct-execution/status|/me/clear-trades" \
  | tail -n 1200 || true

section "FRONTEND RUNTIME MARKERS"
"${COMPOSE[@]}" exec -T frontend sh -lc '
  grep -E "browser-direct|direct-hard-stop|direct-continuity|direct-ledger|run-reset|single-start-stop|scheduler-start-stop" /usr/share/nginx/html/index.html || true
' || true

section "REPORT COMPLETE"
echo "report_file=$REPORT"
echo "Paste or upload this report back into ChatGPT."
