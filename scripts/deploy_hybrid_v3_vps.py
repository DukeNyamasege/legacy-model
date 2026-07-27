from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT = Path("/root/legacy-model")
NEW_RUN = "hybrid_o2u7_put_v2"
V3_VERSION = "HYBRID-O2-U7-RECENT20-PUTFIX-V3"
APP_ID = "33MmAtDICSKcC7LAZj7JO"


class DeployError(RuntimeError):
    pass


def banner(text: str) -> None:
    print(f"\n{'=' * 64}\n{text}\n{'=' * 64}", flush=True)


def run(
    args: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    input_text: str | None = None,
    stdout_file=None,
) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(args), flush=True)
    result = subprocess.run(
        args,
        cwd=PROJECT,
        text=True,
        input=input_text,
        stdout=(subprocess.PIPE if capture else stdout_file),
        stderr=(subprocess.STDOUT if capture else None),
        check=False,
    )
    if capture and result.stdout:
        print(result.stdout.rstrip(), flush=True)
    if check and result.returncode != 0:
        raise DeployError(f"command failed ({result.returncode}): {' '.join(args)}")
    return result


def output(args: list[str]) -> str:
    result = subprocess.run(
        args,
        cwd=PROJECT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise DeployError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}"
        )
    return result.stdout.strip()


def db_psql(sql: str, *, tuples_only: bool = False) -> str:
    flags = "-At" if tuples_only else "-P pager=off"
    command = (
        f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
        f'-v ON_ERROR_STOP=1 {flags}'
    )
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "database", "sh", "-lc", command],
        cwd=PROJECT,
        text=True,
        input=sql,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise DeployError(f"PostgreSQL command failed:\n{result.stdout}")
    return result.stdout.strip()


def db_scalar(sql: str) -> str:
    return db_psql(sql, tuples_only=True).strip()


def stop_worker() -> None:
    subprocess.run(
        ["docker", "compose", "stop", "worker"],
        cwd=PROJECT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def wait_database() -> None:
    for _ in range(30):
        result = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "database",
                "sh",
                "-lc",
                'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            ],
            cwd=PROJECT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(2)
    raise DeployError("PostgreSQL did not become ready")


def update_env(path: Path) -> None:
    updates = {
        "RF_STRATEGY_RUN_ID": NEW_RUN,
        "DERIV_ENVIRONMENT": "demo",
        "TRADING_MODE": "demo",
        "ALLOW_REAL_TRADING": "false",
        "PRODUCTION_ACKNOWLEDGEMENT": "",
        "DERIV_TRADING_ENABLED": "true",
        "DERIV_APP_ID": APP_ID,
        "DERIV_OAUTH_CLIENT_ID": APP_ID,
        "DERIV_OAUTH_REDIRECT_URL": "https://derivadmin.site/oauth/callback",
        "DERIV_APP_MARKUP_PERCENTAGE": "3.0",
        "VIRTUAL_PROTECTION_ENABLED": "true",
        "VIRTUAL_TRIGGER_ACTUAL_LOSSES": "2",
        "VIRTUAL_EXIT_AFTER_WINS": "2",
    }
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    seen: set[str] = set()
    lines: list[str] = []
    for line in old.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            if key not in seen:
                lines.append(f"{key}={updates[key]}")
                seen.add(key)
        else:
            lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def health_ok() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def dashboard_zero_check() -> None:
    with urllib.request.urlopen(
        "http://127.0.0.1:8080/metrics/summary?mode=demo", timeout=30
    ) as response:
        payload = json.load(response)
    if payload.get("snapshot_unavailable"):
        raise DeployError("dashboard snapshot is unavailable")
    consistency = payload.get("data_consistency") or {}
    today = (payload.get("system_performance") or {}).get("today") or {}
    if consistency.get("invariant_ok") is not True:
        raise DeployError(f"dashboard consistency invariant failed: {consistency}")
    expected_zero = {
        "total_trades": int(today.get("total_trades") or 0),
        "wins": int(today.get("wins") or 0),
        "losses": int(today.get("losses") or 0),
    }
    if any(expected_zero.values()):
        raise DeployError(f"dashboard did not start at zero: {expected_zero}")
    if abs(float(today.get("fixed_pnl") or 0.0)) > 1e-9:
        raise DeployError(f"fixed P/L not zero: {today}")
    if abs(float(today.get("martingale_pnl") or 0.0)) > 1e-9:
        raise DeployError(f"recovery P/L not zero: {today}")
    max_stake = float(today.get("maximum_martingale_stake") or 0.50)
    if abs(max_stake - 0.50) > 1e-9:
        raise DeployError(f"canonical maximum stake is not $0.50: {max_stake}")
    print("Dashboard zero/accounting invariant: PASSED", flush=True)


def main() -> int:
    expected = os.environ.get("EXPECTED_COMMIT", "").strip()
    if not expected:
        raise DeployError("EXPECTED_COMMIT is required")
    if not PROJECT.is_dir():
        raise DeployError(f"project directory not found: {PROJECT}")
    os.chdir(PROJECT)

    current = output(["git", "rev-parse", "HEAD"])
    if current != expected:
        raise DeployError(f"source mismatch: HEAD={current}, expected={expected}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path(f"/root/legacy-model-backups/pre-hybrid-v3-{stamp}")
    backup.mkdir(parents=True, exist_ok=False)
    backup.chmod(0o700)

    banner("HYBRID O2/U7 + FIXED-BASE PUT RECOVERY V3")
    print(f"Commit: {current}", flush=True)
    print("Rollout: DEMO ONLY", flush=True)

    banner("1. STOP API + WORKER; KEEP DATABASE")
    run(["docker", "compose", "stop", "worker", "api"], check=False)
    run(["docker", "compose", "up", "-d", "database"])
    wait_database()

    banner("2. VERIFY ZERO OPEN MONEY CONTRACTS")
    open_contracts = int(
        db_scalar("SELECT COUNT(*) FROM trades WHERE settlement_time IS NULL;\n") or "0"
    )
    print(f"Open contracts: {open_contracts}", flush=True)
    if open_contracts != 0:
        raise DeployError("unresolved monetary contracts still exist")

    banner("3. FULL DATABASE + ENV BACKUP")
    db_backup = backup / "database-before-v3.sql"
    with db_backup.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "database",
                "sh",
                "-lc",
                'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            ],
            cwd=PROJECT,
            text=True,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0 or not db_backup.exists() or db_backup.stat().st_size == 0:
        raise DeployError(f"database backup failed: {result.stderr}")
    if (PROJECT / ".env").exists():
        shutil.copy2(PROJECT / ".env", backup / "env.before-v3")
        (backup / "env.before-v3").chmod(0o600)
    (backup / "source-commit.txt").write_text(current + "\n", encoding="utf-8")
    accounts_before = int(db_scalar("SELECT COUNT(*) FROM managed_accounts;\n") or "0")
    if accounts_before <= 0:
        raise DeployError("no managed accounts were found")
    print(f"Registered accounts: {accounts_before}", flush=True)
    print(f"Backup: {db_backup}", flush=True)

    banner("4. FORCE SAFE DEMO V3 ENVIRONMENT")
    update_env(PROJECT / ".env")
    print("Safety keys updated without printing secrets.", flush=True)

    banner("5. RESET UNSAFE V2 TRADING/RECOVERY HISTORY")
    reset_sql = (PROJECT / "scripts/reset_trading_data.sql").read_text(encoding="utf-8")
    print(db_psql(reset_sql), flush=True)
    db_psql(
        """
        INSERT INTO runtime_preferences(preference_key, preference_value, updated_at)
        VALUES ('trading_mode', 'demo', NOW())
        ON CONFLICT (preference_key)
        DO UPDATE SET preference_value='demo', updated_at=NOW();
        """
    )
    accounts_after = int(db_scalar("SELECT COUNT(*) FROM managed_accounts;\n") or "0")
    if accounts_after != accounts_before:
        raise DeployError(
            f"managed account count changed: {accounts_before} -> {accounts_after}"
        )
    zero = int(
        db_scalar(
            """
            SELECT
              (SELECT COUNT(*) FROM trades) +
              (SELECT COUNT(*) FROM system_model_trades) +
              (SELECT COUNT(*) FROM candidate_signals) +
              (SELECT COUNT(*) FROM directional_signals) +
              (SELECT COUNT(*) FROM virtual_trades) +
              (SELECT COUNT(*) FROM account_risk_states) +
              (SELECT COUNT(*) FROM dashboard_snapshots);
            """
        )
        or "0"
    )
    if zero != 0:
        raise DeployError(f"trading/recovery ledgers did not reset to zero: {zero}")
    print(f"Accounts preserved: {accounts_after}", flush=True)
    print("Trading/model/recovery ledgers: ZERO", flush=True)

    banner("6. SOURCE + PYTHON SYNTAX CHECK")
    required_text = {
        "config.yaml": [
            "run_id: hybrid_o2u7_put_v2",
            "maximum_recovery_balance_fraction: 0.10",
            "real_enabled: false",
        ],
        "docker-compose.yml": ["uvicorn app.api_v3:app"],
        "app/hybrid_safety.py": [
            'HYBRID_V3_VERSION = "HYBRID-O2-U7-RECENT20-PUTFIX-V3"'
        ],
    }
    for filename, needles in required_text.items():
        text = (PROJECT / filename).read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                raise DeployError(f"source invariant missing in {filename}: {needle}")
    run(
        [
            sys.executable,
            "-m",
            "py_compile",
            "app/model_accounting.py",
            "app/hybrid_safety.py",
            "app/hybrid_recent_digit_bias.py",
            "app/hybrid_runtime_config.py",
            "app/hybrid_data_integrity.py",
            "app/api_v3.py",
            "app/worker.py",
            "scripts/preflight_hybrid_v3.py",
            "scripts/deploy_hybrid_v3_vps.py",
        ]
    )

    banner("7. BUILD EXACT V3 API + WORKER IMAGES")
    run(["docker", "compose", "build", "api", "worker"])

    banner("8. NO-TRADE V3 SAFETY PREFLIGHT")
    run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "worker",
            "python",
            "scripts/preflight_hybrid_v3.py",
        ]
    )
    money_after_preflight = int(
        db_scalar(
            """
            SELECT
              (SELECT COUNT(*) FROM trades) +
              (SELECT COUNT(*) FROM system_model_trades) +
              (SELECT COUNT(*) FROM virtual_trades) +
              (SELECT COUNT(*) FROM account_risk_states);
            """
        )
        or "0"
    )
    if money_after_preflight != 0:
        raise DeployError("preflight left monetary/recovery state behind")
    stale_state = int(
        db_scalar(
            """
            SELECT COUNT(*) FROM runtime_preferences
            WHERE preference_key IN (
              'hybrid_o2u7_put_v1:state',
              'hybrid_o2u7_put_v2:state'
            );
            """
        )
        or "0"
    )
    if stale_state != 0:
        raise DeployError("hybrid recovery state exists before startup")
    print("Preflight cleanup/state: PASSED", flush=True)

    banner("9. START V3 API ONLY + VERIFY ZERO DASHBOARD")
    run(["docker", "compose", "up", "-d", "--force-recreate", "api"])
    for _ in range(45):
        if health_ok():
            break
        time.sleep(2)
    else:
        print(output(["docker", "compose", "logs", "--tail=250", "api"]), flush=True)
        raise DeployError("V3 API health check failed")
    dashboard_zero_check()
    mode = db_scalar(
        "SELECT preference_value FROM runtime_preferences "
        "WHERE preference_key='trading_mode';\n"
    )
    if mode != "demo":
        raise DeployError(f"runtime trading mode is not demo: {mode!r}")
    prestart_recovery = int(
        db_scalar(
            """
            SELECT COUNT(*) FROM account_risk_states
            WHERE recovery_loss_debt > 0.009
               OR recovery_pending = TRUE
               OR recovery_attempt_active = TRUE
               OR protection_mode <> 'NORMAL_MODE';
            """
        )
        or "0"
    )
    if prestart_recovery != 0:
        raise DeployError("recovery state exists immediately before V3 worker start")

    banner("10. START WORKER ONLY AFTER ALL SAFETY CHECKS")
    run(["docker", "compose", "up", "-d", "--force-recreate", "worker"])
    safety_ok = False
    primary_ok = False
    logs = ""
    for _ in range(45):
        logs = output(["docker", "compose", "logs", "--since=3m", "worker"])
        safety_ok = "HYBRID_SAFETY_ACTIVE" in logs and V3_VERSION in logs
        active_lines = "\n".join(
            line for line in logs.splitlines() if "HYBRID_O2U7_PUT_ACTIVE" in line
        )
        primary_ok = V3_VERSION in active_lines and "mode=PRIMARY_DIGITS" in active_lines
        if safety_ok and primary_ok:
            break
        time.sleep(2)
    if not safety_ok:
        raise DeployError("HYBRID_SAFETY_ACTIVE V3 marker was not found")
    if not primary_ok:
        raise DeployError("V3 worker did not start in PRIMARY_DIGITS")

    combined_logs = output(
        ["docker", "compose", "logs", "--since=5m", "api", "worker"]
    )
    fatal_pattern = re.compile(
        r"Traceback|CRITICAL|HYBRID_SAFETY_INVARIANT_FAILED|"
        r"StringDataRightTruncation|ForeignKeyViolation|IntegrityError|"
        r"HYBRID_DIGIT_ARBITRATION_FAILED"
    )
    fatal = [line for line in combined_logs.splitlines() if fatal_pattern.search(line)]
    if fatal:
        print("\n".join(fatal[-100:]), flush=True)
        raise DeployError("fatal V3 runtime error detected")

    banner("11. FINAL STATUS")
    run(["docker", "compose", "ps"])
    worker_logs = output(["docker", "compose", "logs", "--since=5m", "worker"])
    interesting = re.compile(
        r"HYBRID_SAFETY_ACTIVE|HYBRID_O2U7_PUT_ACTIVE|HYBRID_RECENT_DIGIT|"
        r"HYBRID_FIXED_RECOVERY|VIRTUAL_|PURCHASE|WIN|LOSS|ERROR|WARNING"
    )
    recent = [line for line in worker_logs.splitlines() if interesting.search(line)]
    if recent:
        print("\nRecent V3 activity:\n" + "\n".join(recent[-300:]), flush=True)

    banner("✅ V3 DEPLOYMENT PASSED ALL PRE-TRADE SAFETY CHECKS")
    print(f"Commit              : {expected}")
    print(f"Run ID              : {NEW_RUN}")
    print("Environment         : DEMO ONLY")
    print("Started mode        : PRIMARY_DIGITS")
    print("Primary             : OVER 2 / UNDER 7, recent 20")
    print("Recovery            : strict PUT 15 -> 5 -> 1")
    print("Recovery stake      : CONFIGURED BASE STAKE ONLY")
    print("Debt stake scaling  : DISABLED")
    print("Virtual protection  : 2 losses -> 2 consecutive virtual wins")
    print("Canonical accounting: FIXED AND COHERENT")
    print(f"Registered accounts : {accounts_after} PRESERVED")
    print(f"Backup              : {db_backup}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeployError as exc:
        print(f"\nDEPLOYMENT ERROR: {exc}", file=sys.stderr, flush=True)
        stop_worker()
        print("Worker has been stopped. Database/accounts remain preserved.", flush=True)
        raise SystemExit(1)
