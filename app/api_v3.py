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
from app.custom_martingale import install_custom_martingale_api  # noqa: E402
from app.dashboard_readability import install_dashboard_readability  # noqa: E402
from app.database_runtime_hardening import (  # noqa: E402
    install_database_runtime_hardening,
)
from app.global_reference_dashboard import install_global_reference_dashboard  # noqa: E402
from app.oauth_session_recovery import install_oauth_session_recovery  # noqa: E402
from app.personal_autotrade_start_fix import (  # noqa: E402
    install_personal_autotrade_start_fix,
)
from app.personal_me_session_fix import install_personal_me_session_fix  # noqa: E402
from app.production_integration_hardening import (  # noqa: E402
    install_production_integration_hardening,
)
from app.profit_accuracy_guard import install_profit_accuracy_guard  # noqa: E402

# The lifecycle routes and dashboard consistency wrappers are now loaded. Install
# final guards afterwards so no older compatibility layer can override them.
install_account_mode_execution_lock()
install_profit_accuracy_guard()
install_personal_autotrade_start_fix()

# Advanced Martingale remains account-scoped. System exact-debt recovery is the
# default; custom multiplier and flat-stake profiles are explicit user choices.
install_custom_martingale_api()

# Global Bot Statistics must be a standard reference-model replay. A trader using
# a $1,000 or $3,000 personal stake must not inflate public model P/L or maximum
# stake. Personal stakes remain visible in personal dashboards and simulations.
install_global_reference_dashboard()

# Install the production dashboard/OAuth boundary first, then replace only the
# final OAuth start/callback routes with resilient PKCE session handling. The
# recovery layer keeps one-time server-side validation and issues a host-only
# browser session cookie so the personal dashboard survives www/domain mismatches.
install_production_integration_hardening(app)
install_oauth_session_recovery(app)

# The final personal `/me` route must be installed after every compatibility layer.
# It preserves account-mode consistency and custom Martingale settings, while
# avoiding the unresolved Request annotation that caused 422 responses.
install_personal_me_session_fix(app)

# Readability is installed after the production dashboard route so the final HTML
# always includes the high-contrast text boost and Global $0.50 reference note.
install_dashboard_readability(app)

# Database failures are converted into controlled 503 responses after every final
# route has been installed.
install_database_runtime_hardening(app)
