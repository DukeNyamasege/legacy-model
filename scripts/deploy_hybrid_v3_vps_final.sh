#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_COMMIT="${EXPECTED_COMMIT:-}"
PROJECT_DIR="/root/legacy-model"
NEW_RUN="hybrid_o2u7_put_v2"
V3_VERSION="HYBRID-O2-U7-RECENT20-PUTFIX-V3"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/root/legacy-model-backups/pre-hybrid-v3-${STAMP}"

cd "$PROJECT_DIR"

fail() {
    local message="${1:-unknown error}"
    echo
    echo "============================================================"
    echo "❌ V3 DEPLOYMENT STOPPED SAFELY"
    echo "============================================================"
    echo "$message"
    docker compose stop worker >/dev/null 2>&1 || true
    echo "Worker: STOPPED"
    echo "Database volume: PRESERVED"
    echo "Backup directory: $BACKUP_DIR"
    echo "============================================================"
    exit 1
}
trap 'fail "Unexpected failure near line $LINENO"' ERR

[[ -n "$EXPECTED_COMMIT" ]] || fail "EXPECTED_COMMIT was not supplied"
[[ "$(git rev-parse HEAD)" == "$EXPECTED_COMMIT" ]] \
    || fail "Git HEAD does not match EXPECTED_COMMIT"

echo "============================================================"
echo "HYBRID O2/U7 + FIXED-BASE PUT RECOVERY V3"
echo "Commit: $EXPECTED_COMMIT"
echo "Rollout: DEMO ONLY"
echo "============================================================"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo
echo "=== 1. STOP APPLICATION CONTAINERS ==="
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
|| fail "PostgreSQL is unavailable"

echo
echo "=== 2. VERIFY NO OPEN MONEY CONTRACT ==="
OPEN="$({ docker compose exec -T database sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc \
"SELECT COUNT(*) FROM trades WHERE settlement_time IS NULL;"
'; } | tr -d '[:space:]')"
OPEN="${OPEN:-0}"
echo "Open contracts: $OPEN"
[[ "$OPEN" == "0" ]] || fail "There are unresolved monetary contracts"

echo
echo "=== 3. BACK UP DATABASE + ENV ==="
docker compose exec -T database sh -lc \
'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
> "$BACKUP_DIR/database-before-v3.sql"
test -s "$BACKUP_DIR/database-before-v3.sql" || fail "Database backup failed"
if [[ -f .env ]]; then
    cp -a .env "$BACKUP_DIR/env.before-v3"
    chmod 600 "$BACKUP_DIR/env.before-v3"
fi
git rev-parse HEAD > "$BACKUP_DIR/source-commit.txt"

ACCOUNTS_BEFORE="$({ docker compose exec -T database sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc \
"SELECT COUNT(*) FROM managed_accounts;"
'; } | tr -d '[:space:]')"
[[ "${ACCOUNTS_BEFORE:-0}" -gt 0 ]] || fail "No managed accounts found"
echo "Registered accounts: $ACCOUNTS_BEFORE"
echo "Backup: $BACKUP_DIR/database-before-v3.sql"

echo
echo "=== 4. FORCE SAFE DEMO V3 ENVIRONMENT ==="
python3 - <<'PY'
from pathlib import Path
p = Path('.env')
old = p.read_text(encoding='utf-8') if p.exists() else ''
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
seen = set()
out = []
for line in old.splitlines():
    if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
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
p.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')
PY
chmod 600 .env
echo "Safety keys written; secrets were not printed."

echo
echo "=== 5. RESET UNSAFE V2 TRADING/RECOVERY HISTORY ==="
docker compose exec -T database sh -lc \
'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
< scripts/reset_trading_data.sql

docker compose exec -T database sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "
INSERT INTO runtime_preferences(preference_key, preference_value, updated_at)
VALUES ('\''trading_mode'\', '\''demo'\', NOW())
ON CONFLICT (preference_key)
DO UPDATE SET preference_value='\''demo'\', updated_at=NOW();
"
'

ACCOUNTS_AFTER="$({ docker compose exec -T database sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc \
"SELECT COUNT(*) FROM managed_accounts;"
'; } | tr -d '[:space:]')"
[[ "$ACCOUNTS_AFTER" == "$ACCOUNTS_BEFORE" ]] \
    || fail "Managed account count changed during reset"

echo "Accounts preserved: $ACCOUNTS_AFTER"

ZERO="$({ docker compose exec -T database sh -lc '
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
'; } | tr -d '[:space:]')"
[[ "${ZERO:-1}" == "0" ]] || fail "Trading/recovery ledgers did not reset to zero"

echo "Trading/model/recovery ledgers: ZERO"

echo
echo "=== 6. SOURCE + SHELL + PYTHON SYNTAX ==="
bash -n scripts/deploy_hybrid_v3_vps_final.sh
python3 -m py_compile \
 app/model_accounting.py \
 app/hybrid_safety.py \
 app/hybrid_recent_digit_bias.py \
 app/hybrid_runtime_config.py \
 app/hybrid_data_integrity.py \
 app/api_v3.py \
 app/worker.py \
 scripts/preflight_hybrid_v3.py

grep -Fq 'run_id: hybrid_o2u7_put_v2' config.yaml
grep -Fq 'maximum_recovery_balance_fraction: 0.10' config.yaml
grep -Fq 'real_enabled: false' config.yaml
grep -Fq 'uvicorn app.api_v3:app' docker-compose.yml
echo "Static checks: PASSED"

echo
echo "=== 7. BUILD EXACT V3 API + WORKER IMAGES ==="
docker compose build api worker

echo
echo "=== 8. NO-TRADE SAFETY PREFLIGHT ==="
docker compose run --rm worker python scripts/preflight_hybrid_v3.py

MONEY_AFTER_PREFLIGHT="$({ docker compose exec -T database sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "
SELECT
 (SELECT COUNT(*) FROM trades) +
 (SELECT COUNT(*) FROM system_model_trades) +
 (SELECT COUNT(*) FROM virtual_trades) +
 (SELECT COUNT(*) FROM account_risk_states);
"
'; } | tr -d '[:space:]')"
[[ "${MONEY_AFTER_PREFLIGHT:-1}" == "0" ]] \
    || fail "Safety preflight left monetary/recovery state behind"

STALE="$({ docker compose exec -T database sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "
SELECT COUNT(*) FROM runtime_preferences
WHERE preference_key IN ('\''hybrid_o2u7_put_v1:state'\', '\''hybrid_o2u7_put_v2:state'\');
"
'; } | tr -d '[:space:]')"
[[ "${STALE:-1}" == "0" ]] || fail "Hybrid recovery state exists before startup"
echo "Preflight cleanup/state: PASSED"

echo
echo "=== 9. START API ONLY + VERIFY ZERO DASHBOARD ==="
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
    fail "API health check failed"
}

curl -fsS --max-time 30 \
 'http://127.0.0.1:8080/metrics/summary?mode=demo' \
 -o /tmp/hybrid-v3-zero-dashboard.json
python3 - <<'PY'
import json
p = json.load(open('/tmp/hybrid-v3-zero-dashboard.json', encoding='utf-8'))
assert not p.get('snapshot_unavailable'), p
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

MODE="$({ docker compose exec -T database sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc \
"SELECT preference_value FROM runtime_preferences WHERE preference_key='\''trading_mode'\'';"
'; } | tr -d '[:space:]')"
[[ "$MODE" == "demo" ]] || fail "Runtime mode is not demo"

# This is the definitive inheritance check. It happens immediately before worker start.
PRESTART_RECOVERY="$({ docker compose exec -T database sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "
SELECT COUNT(*) FROM account_risk_states
WHERE recovery_loss_debt > 0.009
   OR recovery_pending = TRUE
   OR recovery_attempt_active = TRUE
   OR protection_mode <> '\''NORMAL_MODE'\'';
"
'; } | tr -d '[:space:]')"
[[ "${PRESTART_RECOVERY:-1}" == "0" ]] || fail "Recovery state exists before V3 worker start"

echo
echo "=== 10. START WORKER ONLY AFTER ALL SAFETY CHECKS ==="
docker compose up -d --force-recreate worker

SAFETY_OK=0
PRIMARY_OK=0
for _ in $(seq 1 45); do
    LOGS="$(docker compose logs --since=3m worker 2>&1 || true)"
    if grep -q 'HYBRID_SAFETY_ACTIVE' <<<"$LOGS" \
       && grep -q "$V3_VERSION" <<<"$LOGS"; then
        SAFETY_OK=1
    fi
    ACTIVE_LINES="$(grep 'HYBRID_O2U7_PUT_ACTIVE' <<<"$LOGS" || true)"
    if grep -q "$V3_VERSION" <<<"$ACTIVE_LINES" \
       && grep -q 'mode=PRIMARY_DIGITS' <<<"$ACTIVE_LINES"; then
        PRIMARY_OK=1
    fi
    [[ "$SAFETY_OK" == "1" && "$PRIMARY_OK" == "1" ]] && break
    sleep 2
done

[[ "$SAFETY_OK" == "1" ]] || {
    docker compose logs --tail=350 worker || true
    fail "V3 safety marker missing"
}
[[ "$PRIMARY_OK" == "1" ]] || {
    docker compose logs --tail=350 worker || true
    fail "V3 worker did not start in PRIMARY_DIGITS"
}

FATAL="$(docker compose logs --since=5m api worker 2>&1 \
 | grep -E 'Traceback|CRITICAL|HYBRID_SAFETY_INVARIANT_FAILED|StringDataRightTruncation|ForeignKeyViolation|IntegrityError|HYBRID_DIGIT_ARBITRATION_FAILED' \
 | tail -100 || true)"
if [[ -n "$FATAL" ]]; then
    echo "$FATAL"
    fail "Fatal V3 runtime error detected"
fi

echo
echo "=== 11. FINAL STATUS ==="
docker compose ps

echo
echo "Recent V3 activity:"
docker compose logs --since=5m worker 2>&1 \
 | grep -E 'HYBRID_SAFETY_ACTIVE|HYBRID_O2U7_PUT_ACTIVE|HYBRID_RECENT_DIGIT|HYBRID_FIXED_RECOVERY|VIRTUAL_|PURCHASE|WIN|LOSS|ERROR|WARNING' \
 | tail -300 || true

echo
echo "============================================================"
echo "✅ V3 DEPLOYMENT PASSED ALL PRE-TRADE SAFETY CHECKS"
echo "============================================================"
echo "Commit              : $EXPECTED_COMMIT"
echo "Run ID              : $NEW_RUN"
echo "Environment         : DEMO ONLY"
echo "Started mode        : PRIMARY_DIGITS"
echo "Primary             : OVER 2 / UNDER 7, recent 20"
echo "Recovery            : strict PUT 15 -> 5 -> 1"
echo "Recovery stake      : CONFIGURED BASE STAKE ONLY"
echo "Debt stake scaling  : DISABLED"
echo "Virtual protection  : 2 losses -> 2 consecutive virtual wins"
echo "Canonical accounting: FIXED AND COHERENT"
echo "Registered accounts : $ACCOUNTS_AFTER PRESERVED"
echo "Backup              : $BACKUP_DIR/database-before-v3.sql"
echo "============================================================"
