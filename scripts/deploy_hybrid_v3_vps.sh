#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_COMMIT="${EXPECTED_COMMIT:-}"
NEW_RUN="hybrid_o2u7_put_v2"
V3_VERSION="HYBRID-O2-U7-RECENT20-PUTFIX-V3"
PROJECT_DIR="/root/legacy-model"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/root/legacy-model-backups/pre-hybrid-v3-${STAMP}"
LOG_PREFIX="[HYBRID-V3]"

cd "$PROJECT_DIR"

fail() {
    local message="${1:-unknown deployment error}"
    echo
    echo "============================================================"
    echo "❌ HYBRID V3 DEPLOYMENT STOPPED SAFELY"
    echo "============================================================"
    echo "$message"
    docker compose stop worker >/dev/null 2>&1 || true
    echo "Worker: STOPPED"
    echo "Database: PRESERVED"
    echo "Backup: ${BACKUP_DIR:-not-created}"
    echo "============================================================"
    exit 1
}

trap 'fail "Unexpected command failure near line $LINENO"' ERR

if [[ -z "$EXPECTED_COMMIT" ]]; then
    fail "EXPECTED_COMMIT is required"
fi

CURRENT="$(git rev-parse HEAD)"
if [[ "$CURRENT" != "$EXPECTED_COMMIT" ]]; then
    fail "Source mismatch: HEAD=$CURRENT expected=$EXPECTED_COMMIT"
fi

echo "============================================================"
echo "HYBRID O2/U7 + FIXED-BASE PUT RECOVERY V3"
echo "============================================================"
echo "Commit: $CURRENT"
echo "Mode: DEMO ONLY"
echo "Run ID: $NEW_RUN"
echo "============================================================"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Stop all application execution before touching durable trading state.
echo
echo "=== 1. STOP API + WORKER; KEEP DATABASE ==="
docker compose stop worker api || true
docker compose up -d database

for _ in $(seq 1 30); do
    if docker compose exec -T database sh -lc \
       'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

docker compose exec -T database sh -lc \
'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null \
|| fail "PostgreSQL did not become ready"

# Never erase contract tracking while a monetary contract is unresolved.
echo
echo "=== 2. VERIFY ZERO OPEN MONEY CONTRACTS ==="
OPEN_CONTRACTS="$({
    docker compose exec -T database sh -lc '
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc \
      "SELECT COUNT(*) FROM trades WHERE settlement_time IS NULL;"
    '
} | tr -d '[:space:]')"
OPEN_CONTRACTS="${OPEN_CONTRACTS:-0}"
echo "Open contracts: $OPEN_CONTRACTS"
[[ "$OPEN_CONTRACTS" == "0" ]] || fail "Open monetary contracts still exist"

# Preserve the entire database before the authorized clean trading reset.
echo
echo "=== 3. FULL DATABASE + ENV BACKUP ==="
docker compose exec -T database sh -lc \
'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
> "$BACKUP_DIR/database-before-v3.sql"
test -s "$BACKUP_DIR/database-before-v3.sql" \
|| fail "Database backup is empty"

if [[ -f .env ]]; then
    cp -a .env "$BACKUP_DIR/env.before-v3"
    chmod 600 "$BACKUP_DIR/env.before-v3"
fi

git rev-parse HEAD > "$BACKUP_DIR/source-commit.txt"
echo "Backup: $BACKUP_DIR"

ACCOUNTS_BEFORE="$({
    docker compose exec -T database sh -lc '
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc \
      "SELECT COUNT(*) FROM managed_accounts;"
    '
} | tr -d '[:space:]')"
echo "Registered accounts before reset: $ACCOUNTS_BEFORE"
[[ "${ACCOUNTS_BEFORE:-0}" -gt 0 ]] || fail "No managed accounts found"

# Rewrite only explicit non-secret safety/runtime keys. Existing secrets remain intact.
echo
echo "=== 4. FORCE V3 DEMO-ONLY ENVIRONMENT ==="
python3 - <<'PY'
from pathlib import Path

path = Path('.env')
text = path.read_text(encoding='utf-8') if path.exists() else ''
updates = {
    'RF_STRATEGY_RUN_ID': 'hybrid_o2u7_put_v2',
    'DERIV_ENVIRONMENT': 'demo',
    'TRADING_MODE': 'demo',
    'ALLOW_REAL_TRADING': 'false',
    'PRODUCTION_ACKNOWLEDGEMENT': '',
    'DERIV_TRADING_ENABLED': 'true',
    'DERIV_APP_ID': '33MmAtDICSKcC7LAZj7JO',
    'DERIV_OAUTH_CLIENT_ID': '33MmAtDICSKcC7LAZj7JO',
    'DERIV_OAUTH_REDIRECT_URL': 'https://derivadmin.site/oauth/callback',
    'DERIV_APP_MARKUP_PERCENTAGE': '3.0',
    'VIRTUAL_PROTECTION_ENABLED': 'true',
    'VIRTUAL_TRIGGER_ACTUAL_LOSSES': '2',
    'VIRTUAL_EXIT_AFTER_WINS': '2',
}

lines = text.splitlines()
seen = set()
out = []
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith('#') or '=' not in line:
        out.append(line)
        continue
    key = line.split('=', 1)[0].strip()
    if key in updates:
        if key not in seen:
            out.append(f'{key}={updates[key]}')
            seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}={value}')
path.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')
PY
chmod 600 .env

echo "Environment safety keys updated without printing secrets."

# Remove the unsafe V2 execution history/debt while preserving client identity/config.
echo
echo "=== 5. CLEAN ALL TRADING/RECOVERY LEDGERS ==="
docker compose exec -T database sh -lc \
'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
< scripts/reset_trading_data.sql

# The clean rollout is explicitly demo. Reset script intentionally preserves general
# preferences, so overwrite trading_mode separately rather than deleting preferences.
docker compose exec -T database sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "
INSERT INTO runtime_preferences(preference_key, preference_value, updated_at)
VALUES ('\''trading_mode'\'', '\''demo'\'', NOW())
ON CONFLICT (preference_key)
DO UPDATE SET preference_value='\''demo'\'', updated_at=NOW();
"
'

ACCOUNTS_AFTER="$({
    docker compose exec -T database sh -lc '
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc \
      "SELECT COUNT(*) FROM managed_accounts;"
    '
} | tr -d '[:space:]')"
echo "Registered accounts after reset: $ACCOUNTS_AFTER"
[[ "$ACCOUNTS_AFTER" == "$ACCOUNTS_BEFORE" ]] \
|| fail "Managed account count changed during reset"

# Explicit zero-state invariant. test_runs may become 1 only after preflight initializes V3.
ZERO_SUM="$({
    docker compose exec -T database sh -lc '
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "
      SELECT
        (SELECT COUNT(*) FROM trades) +
        (SELECT COUNT(*) FROM system_model_trades) +
        (SELECT COUNT(*) FROM candidate_signals) +
        (SELECT COUNT(*) FROM directional_signals) +
        (SELECT COUNT(*) FROM virtual_trades) +
        (SELECT COUNT(*) FROM account_risk_states) +
        (SELECT COUNT(*) FROM dashboard_snapshots);
      "
    '
} | tr -d '[:space:]')"
[[ "${ZERO_SUM:-1}" == "0" ]] || fail "Trading ledgers are not zero after reset"

echo "Trading/recovery/model ledgers: ZERO"

# Syntax checks use host Python only as a parser; imports are tested inside Docker.
echo
echo "=== 6. SOURCE + SYNTAX INVARIANTS ==="
grep -Fq 'HYBRID_V3_VERSION = "HYBRID-O2-U7-RECENT20-PUTFIX-V3"' app/hybrid_safety.py
grep -Fq 'run_id: hybrid_o2u7_put_v2' config.yaml
grep -Fq 'maximum_recovery_balance_fraction: 0.10' config.yaml
grep -Fq 'real_enabled: false' config.yaml
grep -Fq 'uvicorn app.api_v3:app' docker-compose.yml
python3 -m py_compile \
    app/model_accounting.py \
    app/hybrid_safety.py \
    app/hybrid_recent_digit_bias.py \
    app/hybrid_runtime_config.py \
    app/hybrid_data_integrity.py \
    app/api_v3.py \
    app/worker.py \
    scripts/preflight_hybrid_v3.py

echo "Source/syntax: PASSED"

# Build exactly what will run in production. No application container is started yet.
echo
echo "=== 7. BUILD API + WORKER ==="
docker compose build api worker

# This test deliberately injects a $1,000 fake debt into one account, proves that the
# planned recovery stake remains $0.50, proves virtual mode blocks monetary buying,
# and deletes the fake state in a finally block. It makes no Deriv purchase.
echo
echo "=== 8. NO-TRADE V3 SAFETY PREFLIGHT ==="
docker compose run --rm worker python scripts/preflight_hybrid_v3.py

# Preflight creates a TestRun/BotState only. It must leave monetary/risk data clean.
echo
echo "=== 9. VERIFY PREFLIGHT LEFT NO MONEY/RISK STATE ==="
PREFLIGHT_SUM="$({
    docker compose exec -T database sh -lc '
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "
      SELECT
        (SELECT COUNT(*) FROM trades) +
        (SELECT COUNT(*) FROM system_model_trades) +
        (SELECT COUNT(*) FROM account_risk_states) +
        (SELECT COUNT(*) FROM virtual_trades);
      "
    '
} | tr -d '[:space:]')"
[[ "${PREFLIGHT_SUM:-1}" == "0" ]] || fail "Preflight left monetary/recovery rows behind"

STALE_STATE="$({
    docker compose exec -T database sh -lc '
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "
      SELECT COUNT(*) FROM runtime_preferences
      WHERE preference_key IN ('\''hybrid_o2u7_put_v1:state'\'', '\''hybrid_o2u7_put_v2:state'\'');
      "
    '
} | tr -d '[:space:]')"
[[ "${STALE_STATE:-1}" == "0" ]] || fail "Hybrid recovery state exists before worker start"

echo "Preflight cleanup: PASSED"

# Start API only and prove dashboard/model accounting begins at zero.
echo
echo "=== 10. START V3 API ONLY ==="
docker compose up -d --force-recreate api
API_OK=0
for _ in $(seq 1 45); do
    if curl -fsS --max-time 3 http://127.0.0.1:8080/health >/dev/null 2>&1; then
        API_OK=1
        break
    fi
    sleep 2
done
[[ "$API_OK" == "1" ]] || {
    docker compose logs --tail=250 api || true
    fail "V3 API health check failed"
}

curl -fsS --max-time 30 \
    'http://127.0.0.1:8080/metrics/summary?mode=demo' \
    -o /tmp/hybrid-v3-zero-dashboard.json

python3 - <<'PY'
import json
p = json.load(open('/tmp/hybrid-v3-zero-dashboard.json', encoding='utf-8'))
if p.get('snapshot_unavailable'):
    raise SystemExit('dashboard snapshot unavailable')
c = p.get('data_consistency') or {}
t = (p.get('system_performance') or {}).get('today') or {}
assert c.get('invariant_ok') is True, c
assert int(t.get('total_trades') or 0) == 0, t
assert int(t.get('wins') or 0) == 0, t
assert int(t.get('losses') or 0) == 0, t
assert abs(float(t.get('fixed_pnl') or 0)) < 1e-9, t
assert abs(float(t.get('martingale_pnl') or 0)) < 1e-9, t
assert abs(float(t.get('maximum_martingale_stake') or 0.50) - 0.50) < 1e-9, t
print('Dashboard zero/accounting invariant: PASSED')
PY

# Re-confirm runtime mode was not changed by API initialization.
MODE="$({
    docker compose exec -T database sh -lc '
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc \
      "SELECT preference_value FROM runtime_preferences WHERE preference_key='\''trading_mode'\'';"
    '
} | tr -d '[:space:]')"
[[ "$MODE" == "demo" ]] || fail "Database runtime trading mode is not demo"

# Only now is the trading worker allowed to start.
echo
echo "=== 11. START V3 WORKER ==="
docker compose up -d --force-recreate worker

SAFETY_OK=0
PRIMARY_OK=0
for _ in $(seq 1 45); do
    LOGS="$(docker compose logs --since=3m worker 2>&1 || true)"
    if grep -q 'HYBRID_SAFETY_ACTIVE' <<<"$LOGS" \
       && grep -q "$V3_VERSION" <<<"$LOGS"; then
        SAFETY_OK=1
    fi
    if grep 'HYBRID_O2U7_PUT_ACTIVE' <<<"$LOGS" \
       | grep -q "$V3_VERSION" \
       && grep 'HYBRID_O2U7_PUT_ACTIVE' <<<"$LOGS" \
       | grep -q 'mode=PRIMARY_DIGITS'; then
        PRIMARY_OK=1
    fi
    if [[ "$SAFETY_OK" == "1" && "$PRIMARY_OK" == "1" ]]; then
        break
    fi
    sleep 2
done

[[ "$SAFETY_OK" == "1" ]] || {
    docker compose logs --tail=350 worker || true
    fail "HYBRID_SAFETY_ACTIVE V3 marker missing"
}
[[ "$PRIMARY_OK" == "1" ]] || {
    docker compose logs --tail=350 worker || true
    fail "Worker did not start V3 in PRIMARY_DIGITS"
}

FATAL="$(
    docker compose logs --since=5m api worker 2>&1 \
    | grep -E 'Traceback|CRITICAL|HYBRID_SAFETY_INVARIANT_FAILED|StringDataRightTruncation|ForeignKeyViolation|IntegrityError|HYBRID_DIGIT_ARBITRATION_FAILED' \
    | tail -100 || true
)"
if [[ -n "$FATAL" ]]; then
    echo "$FATAL"
    fail "Fatal V3 runtime error detected"
fi

# Verify the old dangerous recovery state has not reappeared merely from startup.
RECOVERY_ROWS="$({
    docker compose exec -T database sh -lc '
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "
      SELECT COUNT(*) FROM account_risk_states
      WHERE recovery_loss_debt > 0.009
         OR recovery_pending = TRUE
         OR recovery_attempt_active = TRUE
         OR protection_mode <> '\''NORMAL_MODE'\'';
      "
    '
} | tr -d '[:space:]')"
[[ "${RECOVERY_ROWS:-1}" == "0" ]] || fail "Recovery debt/state appeared before a new V3 loss"

echo
echo "=== 12. FINAL STATUS ==="
docker compose ps

echo
echo "Recent V3 markers:"
docker compose logs --since=5m worker 2>&1 \
| grep -E 'HYBRID_SAFETY_ACTIVE|HYBRID_O2U7_PUT_ACTIVE|HYBRID_RECENT_DIGIT|HYBRID_FIXED_RECOVERY|VIRTUAL_|PURCHASE|WIN|LOSS|ERROR|WARNING' \
| tail -300 || true

echo
echo "============================================================"
echo "✅ HYBRID V3 DEPLOYMENT COMPLETE"
echo "============================================================"
echo "Commit              : $EXPECTED_COMMIT"
echo "Run ID              : $NEW_RUN"
echo "Environment         : DEMO ONLY"
echo "Initial mode        : PRIMARY_DIGITS"
echo "Primary             : OVER 2 / UNDER 7, recent 20"
echo "Recovery            : strict PUT 15 -> 5 -> 1"
echo "Recovery stake      : EACH ACCOUNT'S BASE STAKE ONLY"
echo "Debt stake scaling  : DISABLED"
echo "Virtual protection  : 2 losses -> 2 consecutive virtual wins"
echo "Canonical accounting: COHERENT FIXED-BASE"
echo "Registered accounts : $ACCOUNTS_AFTER PRESERVED"
echo "Backup              : $BACKUP_DIR/database-before-v3.sql"
echo "============================================================"
