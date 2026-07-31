from __future__ import annotations

# Install account generation filtering before app.api creates its repository. The
# API then exposes only registrations from the current enrollment generation while
# historical accounts and their trade relationships remain preserved in PostgreSQL.
from app.account_reenrollment import install_account_reenrollment

install_account_reenrollment()

# Install canonical fixed-base accounting before app.api creates the repository.
# The API and worker therefore read the same account-independent model ledger and
# cannot reintroduce debt-sized Martingale replay in dashboard calculations.
from app.hybrid_safety import install_hybrid_accounting_integrity

install_hybrid_accounting_integrity()

# The private WebSocket OVER-2 path can settle personal Trade rows before the
# canonical system-model row/cache is refreshed. Global dashboard statistics must
# still show one model event per purchased signal, never a false zero.
from app.dashboard_actual_trade_fallback import install_dashboard_actual_trade_fallback

install_dashboard_actual_trade_fallback()

# Production recovery policy: one OVER-2 loss arms PUT; repeat PUT after PUT
# losses; one successful real PUT exits recovery and returns to OVER-2.
from app.one_put_recovery_policy import install_one_put_recovery_policy

install_one_put_recovery_policy()

from app.api_account_lifecycle import app  # noqa: E402
from app.account_mode_execution_lock import install_account_mode_execution_lock  # noqa: E402
from app.database_runtime_hardening import (  # noqa: E402
    install_database_runtime_hardening,
)
from app.production_integration_hardening import (  # noqa: E402
    install_production_integration_hardening,
)

# The lifecycle routes are now loaded. Install the final per-mode guard after the
# repository lifecycle patch so Demo and Real cannot start each other implicitly.
install_account_mode_execution_lock()

# Install the final OAuth/dashboard integration first, then add the final
# database-aware health and exception boundary. No later wrapper may replace
# these production routes or return raw SQLAlchemy connection tracebacks.
install_production_integration_hardening(app)
install_database_runtime_hardening(app)
