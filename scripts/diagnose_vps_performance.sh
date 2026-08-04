#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPORT_DIR="$PROJECT_DIR/performance-reports"
TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
REPORT_FILE=${PERFORMANCE_REPORT_FILE:-"$REPORT_DIR/vps-performance-$TIMESTAMP.log"}
mkdir -p "$REPORT_DIR"
cd "$PROJECT_DIR"

compose() {
  docker compose -f docker-compose.yml -f docker-compose.vps.yml "$@"
}

section() {
  printf '\n============================================================\n%s\n============================================================\n' "$1"
}

endpoint_timing() {
  label=$1
  url=$2
  printf '%-28s ' "$label"
  curl --max-time 15 -sS -o /dev/null \
    -w 'status=%{http_code} dns=%{time_namelookup}s connect=%{time_connect}s start=%{time_starttransfer}s total=%{time_total}s bytes=%{size_download}\n' \
    "$url" 2>&1 || true
}

{
  section "VPS PERFORMANCE DIAGNOSTIC"
  echo "Generated UTC : $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "Project       : $PROJECT_DIR"
  echo "Git commit    : $(git rev-parse HEAD 2>/dev/null || echo unavailable)"
  echo "Kernel        : $(uname -a 2>/dev/null || true)"

  section "FILESYSTEM CAPACITY AND INODES"
  df -h 2>/dev/null || true
  echo ""
  df -i 2>/dev/null || true

  section "HOST LOAD, MEMORY AND I/O PRESSURE"
  uptime 2>/dev/null || true
  echo ""
  free -h 2>/dev/null || true
  echo ""
  if command -v vmstat >/dev/null 2>&1; then
    vmstat 1 5 2>/dev/null || true
  fi
  echo ""
  if command -v iostat >/dev/null 2>&1; then
    iostat -xz 1 3 2>/dev/null || true
  else
    echo "iostat is not installed; vmstat data above remains available."
  fi

  section "DOCKER CAPACITY"
  docker system df -v 2>/dev/null || docker system df 2>/dev/null || true

  section "CONTAINER STATUS AND RESOURCE SNAPSHOT"
  compose ps 2>/dev/null || true
  echo ""
  docker stats --no-stream \
    --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}' \
    2>/dev/null || true
  echo ""
  container_ids=$(compose ps -q 2>/dev/null || true)
  if [ -n "$container_ids" ]; then
    # shellcheck disable=SC2086
    docker inspect -f '{{.Name}} restarts={{.RestartCount}} status={{.State.Status}} started={{.State.StartedAt}}' \
      $container_ids 2>/dev/null || true
  fi

  section "LOCAL API TIMINGS"
  endpoint_timing "health live" "http://127.0.0.1:8080/health/live"
  endpoint_timing "health database" "http://127.0.0.1:8080/health/database"
  endpoint_timing "health ready" "http://127.0.0.1:8080/health/ready"
  endpoint_timing "dashboard demo" "http://127.0.0.1:8080/metrics/summary?mode=demo"
  endpoint_timing "dashboard real" "http://127.0.0.1:8080/metrics/summary?mode=real"
  endpoint_timing "public traders" "http://127.0.0.1:8080/metrics/public-traders"

  section "POSTGRES DATABASE SIZE AND LARGEST TABLES"
  compose exec -T database sh -ec '
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<"SQL"
SELECT current_database() AS database,
       pg_size_pretty(pg_database_size(current_database())) AS database_size;

SELECT relname AS table_name,
       n_live_tup,
       n_dead_tup,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       pg_size_pretty(pg_relation_size(relid)) AS table_size,
       pg_size_pretty(pg_indexes_size(relid)) AS index_size,
       last_autovacuum,
       last_autoanalyze
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 20;

SELECT datname,
       numbackends,
       xact_commit,
       xact_rollback,
       blks_read,
       blks_hit,
       CASE WHEN blks_hit + blks_read = 0 THEN 1
            ELSE round(blks_hit::numeric / (blks_hit + blks_read), 4)
       END AS cache_hit_ratio,
       temp_files,
       pg_size_pretty(temp_bytes) AS temp_bytes,
       deadlocks
FROM pg_stat_database
WHERE datname = current_database();
SQL
  ' 2>&1 || true

  section "POSTGRES CONNECTIONS, LONG QUERIES AND BLOCKING"
  compose exec -T database sh -ec '
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<"SQL"
SELECT state, count(*) AS connections
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY state
ORDER BY state;

SELECT pid,
       usename,
       state,
       wait_event_type,
       wait_event,
       now() - query_start AS query_age,
       left(regexp_replace(query, E$$[\n\r\t]+$$, $$ $$, $$g$$), 220) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> $$idle$$
ORDER BY query_start
LIMIT 20;

SELECT blocked.pid AS blocked_pid,
       blocker.pid AS blocker_pid,
       now() - blocked.query_start AS blocked_for,
       left(blocked.query, 160) AS blocked_query,
       left(blocker.query, 160) AS blocker_query
FROM pg_stat_activity blocked
JOIN pg_locks blocked_locks ON blocked_locks.pid = blocked.pid AND NOT blocked_locks.granted
JOIN pg_locks blocker_locks
  ON blocker_locks.locktype = blocked_locks.locktype
 AND blocker_locks.database IS NOT DISTINCT FROM blocked_locks.database
 AND blocker_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
 AND blocker_locks.page IS NOT DISTINCT FROM blocked_locks.page
 AND blocker_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
 AND blocker_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
 AND blocker_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
 AND blocker_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
 AND blocker_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
 AND blocker_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
 AND blocker_locks.granted
JOIN pg_stat_activity blocker ON blocker.pid = blocker_locks.pid
LIMIT 20;
SQL
  ' 2>&1 || true

  section "POSTGRES CHECKPOINTS AND WAL"
  compose exec -T database sh -ec '
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<"SQL"
SELECT current_setting($$checkpoint_timeout$$) AS checkpoint_timeout,
       current_setting($$checkpoint_completion_target$$) AS checkpoint_completion_target,
       current_setting($$max_wal_size$$) AS max_wal_size,
       current_setting($$shared_buffers$$) AS shared_buffers;
SQL
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT * FROM pg_stat_checkpointer;" 2>/dev/null \
      || psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT checkpoints_timed, checkpoints_req, checkpoint_write_time, checkpoint_sync_time, buffers_checkpoint FROM pg_stat_bgwriter;"
  ' 2>&1 || true

  section "RECENT API SLOW REQUESTS"
  compose logs --since=30m api 2>/dev/null \
    | grep -E 'SLOW_REQUEST|duration_ms=|Traceback|ReadTimeout|DATABASE_' \
    | tail -n 250 || true

  section "RECENT WORKER RESTART OR TRANSPORT ERRORS"
  compose logs --since=30m worker 2>/dev/null \
    | grep -E 'Traceback|ERROR|RECONNECT|INVALID_CONTRACT|MALFORMED|CONNECTION|TIMEOUT' \
    | tail -n 250 || true

  section "DIAGNOSTIC INTERPRETATION MARKERS"
  echo "Disk capacity alone is not marked as the cause unless usage is near exhaustion, inodes are exhausted, or BlockIO/iowait is high."
  echo "Slow local start-transfer times point to API/database work; slow public-only times point to proxy/network latency."
  echo "This report contains no tokens, passwords or environment-variable values."
} | tee "$REPORT_FILE"

chmod 600 "$REPORT_FILE" 2>/dev/null || true
echo "Performance report saved to: $REPORT_FILE"
