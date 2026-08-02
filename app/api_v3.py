from __future__ import annotations

# Install account generation filtering before app.api creates its repository. The
# API then exposes only registrations from the current enrollment generation while
# historical accounts and their trade relationships remain preserved in PostgreSQL.
from app.account_reenrollment import install_account_reenrollment

install_account_reenrollment()

# Install canonical fixed-base accounting before app.api creates its repository.
# The API and worker therefore read the same account-independent model ledger and
# cannot reintroduce debt-sized Martingale replay in dashboard calculations.
from app.hybrid_safety import install_hybrid_accounting_integrity

install_hybrid_accounting_integrity()

# The private WebSocket digit path can settle personal Trade rows before the
# canonical system-model row/cache is refreshed. Global dashboard statistics must
# still show one model event per purchased signal, never a false zero.
from app.dashboard_actual_trade_fallback import install_dashboard_actual_trade_fallback

install_dashboard_actual_trade_fallback()

from app.api_account_lifecycle import app  # noqa: E402
from app.account_identity_ui import install_account_identity_ui  # noqa: E402
from app.account_mode_execution_lock import install_account_mode_execution_lock  # noqa: E402
from app.ai_digit_recovery_v1 import install_ai_digit_recovery_v1_strategy  # noqa: E402
from app.aidr_api_metadata import install_aidr_api_metadata  # noqa: E402
from app.aidr_execution_flow_fix import install_aidr_execution_flow_fix  # noqa: E402
from app.aidr_virtual_settlement_fix import install_aidr_virtual_settlement_fix  # noqa: E402
from app.custom_martingale import install_custom_martingale_api  # noqa: E402
from app.dashboard_loader_unlock import install_dashboard_loader_unlock  # noqa: E402
from app.dashboard_readability import install_dashboard_readability  # noqa: E402
from app.dashboard_settings_guard import install_dashboard_settings_guard  # noqa: E402
from app.dashboard_smoke_compat import install_dashboard_smoke_compat  # noqa: E402
from app.dashboard_stability_fix import install_dashboard_stability_fix  # noqa: E402
from app.database_runtime_hardening import (  # noqa: E402
    install_database_runtime_hardening,
)
from app.final_personal_trade_stream import install_final_personal_trade_stream  # noqa: E402
from app.final_public_controls import install_final_public_controls  # noqa: E402
from app.final_virtual_history_ui import install_final_virtual_history_ui  # noqa: E402
from app.global_reference_dashboard import install_global_reference_dashboard  # noqa: E402
from app.global_reference_dashboard_compat import (  # noqa: E402
    install_global_reference_dashboard_compat,
)
from app.head_request_compat import install_head_request_compat  # noqa: E402
from app.lifecycle_reset_authority import install_lifecycle_reset_authority  # noqa: E402
from app.live_metrics_ui import install_live_metrics_ui  # noqa: E402
from app.model_pnl_display_aliases import install_model_pnl_display_aliases  # noqa: E402
from app.oauth_session_recovery import install_oauth_session_recovery  # noqa: E402
from app.personal_account_identity_balance import (  # noqa: E402
    install_personal_account_identity_balance,
)
from app.personal_autotrade_start_fix import (  # noqa: E402
    install_personal_autotrade_start_fix,
)
from app.personal_me_session_fix import install_personal_me_session_fix  # noqa: E402
from app.personal_virtual_status_api import install_personal_virtual_status_api  # noqa: E402
from app.production_integration_hardening import (  # noqa: E402
    install_production_integration_hardening,
)
from app.profit_accuracy_guard import install_profit_accuracy_guard  # noqa: E402
from app.public_trader_stats_api import install_public_trader_stats_api  # noqa: E402
from app.public_trader_stats_ui import install_public_trader_stats_ui  # noqa: E402
from app.reset_trades_always_ui import install_reset_trades_always_ui  # noqa: E402
from app.settings_persistence_fix import install_settings_persistence_fix  # noqa: E402
from app.simplified_dashboard_api import install_simplified_dashboard_api  # noqa: E402
from app.telegram_silence import install_telegram_silence  # noqa: E402

# Disable channel announcements, private alerts and Telegram polling before any
# later API integration can queue a message. The switch is reversible by env +
# process restart, but it defaults to normal behaviour unless explicitly enabled.
install_telegram_silence()

# The lifecycle routes and dashboard consistency wrappers are now loaded. Install
# final guards afterwards so no older compatibility layer can override them.
install_account_mode_execution_lock()
install_profit_accuracy_guard()
install_personal_autotrade_start_fix()

# Advanced Martingale remains account-scoped. System exact-debt recovery is the
# default; custom multiplier and flat-stake profiles are explicit user choices.
install_custom_martingale_api()

# Patch the AIDR settlement factory before the strategy wraps repository methods.
# This keeps duplicate masked account IDs from receiving another account's virtual
# progress in either process.
install_aidr_virtual_settlement_fix()

# Active public-release strategy metadata: DIGITOVER 1 normal, DIGITOVER 3
# first recovery, then virtual OVER-4 confirmation and one full-debt recovery.
install_ai_digit_recovery_v1_strategy()
install_aidr_execution_flow_fix()

# Global Bot Statistics must be a standard reference-model replay. A trader using
# a $1,000 or $3,000 personal stake must not inflate public model P/L or maximum
# stake. Personal stakes remain visible in personal dashboards and simulations.
install_global_reference_dashboard()
install_global_reference_dashboard_compat()
install_model_pnl_display_aliases()
install_aidr_api_metadata()

# Public platform totals are available whether the browser is logged in or out.
# Unique linked traders are counted separately from currently enabled traders.
install_public_trader_stats_api(app)

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

# Older lightweight API is retained for compatibility during import, then the
# final authorities at the bottom replace its lifecycle and trade-stream routes.
install_simplified_dashboard_api()
install_final_public_controls(app)
install_personal_virtual_status_api(app)

# Install final settings after every older account-settings route. Users must be
# allowed to save stake, TP/SL and Martingale settings before adding a trading
# token; the token is required only before starting execution.
install_settings_persistence_fix(app)

# Show the exact logged-in account ID and refresh personal balances with a short
# throttle so users can identify BOT/ROT accounts and see deductions/settlements
# promptly after trades.
install_personal_account_identity_balance(app)

# Readability is installed after the production dashboard route so the final HTML
# always includes the high-contrast text boost and simplified desktop/mobile UI.
install_dashboard_readability(app)

# Stable mobile dashboard is installed last. It keeps Recent Trades from changing
# structure during silent refreshes and reduces mobile typography.
install_dashboard_stability_fix(app)
install_dashboard_settings_guard(app)

# Keep the deployment smoke tests compatible with the old simplified-dashboard
# marker while the real live dashboard uses the stable dashboard-v2 routes.
install_dashboard_smoke_compat(app)
install_account_identity_ui(app)

# Final UI layer: keep overview and trades-page KPI numbers live without a full
# route rebuild, so balance, P/L, win rate, totals, wins and losses move in real
# time while the stable Recent Trades table remains untouched.
install_live_metrics_ui(app)

# Show public platform stats for everyone, logged in or not: only total registered
# traders and trading now.
install_public_trader_stats_ui(app)

# Keep personal Reset Today / Reset All trade controls visible on Overview and
# Trades pages after every refresh/re-render.
install_reset_trades_always_ui(app)

# Prevent the route loader from staying above an already-rendered dashboard and
# display account-level AIDR/virtual progress without rebuilding the whole page.
install_dashboard_loader_unlock(app)

# Final presentation authority: virtual observations appear inside the same
# Today's Recent Trades / complete-history tables as actual trades, with an
# explicit VIRTUAL OVER 4 badge and $0.00 impact. A permanent risk disclaimer is
# also displayed on every dashboard page.
install_final_virtual_history_ui(app)

# Verification commands use curl -I, which sends HEAD. Browsers use GET, but the
# final dynamic dashboard routes must also answer HEAD with normal headers.
install_head_request_compat(app)

# FINAL API AUTHORITIES. Nothing installed below these may replace their routes:
# Pause preserves state; Stop/Reset clears all AIDR state and leaves execution
# stopped; the trade stream combines actual contracts and visible virtual 1/2,
# 2/2 progress for the exact managed-account row.
install_lifecycle_reset_authority(app)
install_final_personal_trade_stream(app)

# Database failures are converted into controlled 503 responses after every final
# route has been installed.
install_database_runtime_hardening(app)
