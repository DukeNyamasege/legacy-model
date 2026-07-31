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

from app.api_account_lifecycle import app  # noqa: E402
from app.database_runtime_hardening import (  # noqa: E402
    install_database_runtime_hardening,
)
from app.production_integration_hardening import (  # noqa: E402
    install_production_integration_hardening,
)

# Install the final OAuth/dashboard integration first, then add the final
# database-aware health and exception boundary. No later wrapper may replace
# these production routes or return raw SQLAlchemy connection tracebacks.
install_production_integration_hardening(app)
install_database_runtime_hardening(app)
