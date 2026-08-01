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

# Production recovery policy: one OVER-2 loss arms PUT; a failed real PUT enters
# virtual PUT protection until two consecutive virtual wins confirm the next PUT.
from app.one_put_recovery_policy import install_one_put_recovery_policy

install_one_put_recovery_policy()

from app.api_account_lifecycle import app  # noqa: E402
from app.account_mode_execution_lock import install_account_mode_execution_lock  # noqa: E402
from app.database_runtime_hardening import (  # noqa: E402
    install_database_runtime_hardening,
)
from app.personal_autotrade_start_fix import (  # noqa: E402
    install_personal_autotrade_start_fix,
)
from app.production_integration_hardening import (  # noqa: E402
    install_production_integration_hardening,
)
from app.profit_accuracy_guard import install_profit_accuracy_guard  # noqa: E402

# The lifecycle routes and dashboard consistency wrappers are now loaded. Install
# final guards afterwards so no older compatibility layer can override them.
install_account_mode_execution_lock()
install_profit_accuracy_guard()
install_personal_autotrade_start_fix()

# Install the final OAuth/dashboard integration first, then add the final
# database-aware health and exception boundary. No later wrapper may replace
# these production routes or return raw SQLAlchemy connection tracebacks.
install_production_integration_hardening(app)
install_database_runtime_hardening(app)
