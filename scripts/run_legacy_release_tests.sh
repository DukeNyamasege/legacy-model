#!/usr/bin/env sh
set -eu

# test_rf_dir5.py and test_strategy_logic.py deliberately create temporary
# SQLite databases and local token files. A VPS release candidate supplies a
# PostgreSQL DATABASE_URL and disables legacy global tokens for production. If
# those production values leak into the legacy tests, independent test cases
# share rows, duplicate tick keys and cannot construct their local test clients.
# Keep this isolation confined to the one-shot test process; production API and
# worker containers retain their normal PostgreSQL and account-scoped settings.
unset DATABASE_URL
export ALLOW_LEGACY_GLOBAL_TOKENS=true
export COPYTRADING_ALLOW_LEGACY_GLOBAL_TOKENS=true
export FRONTEND_ORIGINS="http://127.0.0.1:8080,http://localhost:8080,https://derivadmin.site,https://legacymodel.netlify.app"

exec python -m unittest -q test_rf_dir5.py test_strategy_logic.py
