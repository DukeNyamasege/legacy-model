from __future__ import annotations

"""Full-VPS production API entrypoint.

The VPS-native backend/realtime route stack is imported first so every compatibility
layer finishes installing before Full-VPS-only observability, Telegram control,
preferences, persistent scheduling, direct-browser execution bootstrap, and the
premium/payment layers wrap the final routes.
"""

from app.vps_core_api import app
from app.automation_preferences_api import install_automation_preferences_api
from app.automation_scheduler_action5 import install_automation_scheduler_action5
from app.automation_scheduler_v2_authority import install_automation_scheduler_v2_authority
from app.final_linked_accounts_6f2 import install_final_linked_accounts_6f2
from app.lipana_mpesa_action6b import install_lipana_mpesa_action6b
from app.premium_access_api import install_premium_access_action6a
from app.premium_renewal_action6d import install_premium_renewal_action6d
from app.public_testing_access import (
    apply_public_testing_premium_bypass,
    apply_public_testing_scheduler_bypass,
    install_public_testing_access_api,
)
from app.text_to_strategy_api import install_text_to_strategy_api
from app.vps_dashboard_latency_hotfix import install_vps_dashboard_latency_hotfix
from app.vps_demo_balance_reset import install_vps_demo_balance_reset
from app.vps_direct_execution_api import install_vps_direct_execution_api
from app.vps_direct_execution_arm_guard import install_vps_direct_execution_arm_guard
from app.vps_direct_execution_checkpoint import install_vps_direct_execution_checkpoint
from app.vps_direct_hard_stop_v2 import install_vps_direct_hard_stop_v2
from app.vps_direct_runtime_rate_limit import install_vps_direct_runtime_rate_limit
from app.vps_fast_execution_controls import install_vps_fast_execution_controls
from app.vps_linked_accounts_latency_hotfix import install_vps_linked_accounts_latency_hotfix
from app.vps_login_observability_hotfix import install_vps_login_observability_hotfix
from app.vps_runtime_policy_hotfix import install_vps_runtime_policy_hotfix
from app.vps_session_observability import install_vps_session_observability
from app.vps_telegram_control import install_vps_telegram_control


# Public testing is deliberately free until the product is declared ready. This
# runs before the final premium middleware is installed and also overrides an old
# VPS .env that may still say PREMIUM_ACCESS_ENFORCEMENT=true. The payment and
# entitlement routes remain installed so the future paid flow is not deleted.
PUBLIC_TESTING_FREE_ACCESS = apply_public_testing_premium_bypass()

install_vps_session_observability(app)
install_vps_login_observability_hotfix()
install_vps_telegram_control(app)
install_vps_dashboard_latency_hotfix(app)
install_text_to_strategy_api(app)
install_automation_preferences_api(app)
# Scheduling deliberately remains server-owned. A scheduled job is durable even
# when the user's browser is closed and therefore continues through the VPS worker.
install_automation_scheduler_action5(app)
# V2 patches the already-installed Action 5 globals before FastAPI lifespan starts:
# exact-second polling, future-session hard-stop clearing, and durable result cards.
install_automation_scheduler_v2_authority()
install_lipana_mpesa_action6b(app)
install_premium_renewal_action6d(app)
# Action 6D keeps its payment/renewal routes, but while testing is free it may not
# skip scheduled starts or pause accounts just because an old premium period ended.
apply_public_testing_scheduler_bypass()
install_public_testing_access_api(app)
# 6F-2 installs the canonical linked-account semantics first. The VPS hotfix then
# keeps expensive all-account discovery off ordinary dashboard polling while exact
# current/target identity validation remains authoritative for an actual switch.
install_final_linked_accounts_6f2(app)
install_vps_linked_accounts_latency_hotfix(app)
# Account management remains a low-frequency control-plane operation. Demo balance
# reset calls Deriv's official REST endpoint with the server-held trade credential;
# no long-lived OAuth/PAT token is exposed to the browser.
install_vps_demo_balance_reset(app)
# Browser-direct transport liveness gets a separate bounded quota. Heartbeats,
# checkpoints and OTP bootstrap must never consume the ordinary 30/min personal
# mutation budget and accidentally expire the browser ownership lease. Origin and
# all other request-security checks remain unchanged.
install_vps_direct_runtime_rate_limit()
# Live/manual execution uses a browser <-> Deriv authenticated WebSocket. The VPS
# only issues the short-lived OTP URL and maintains the browser/server ownership
# lease. The atomic arm guard prevents a second browser/device from sharing that
# financial lease. Small recovery checkpoints preserve state for offline takeover.
install_vps_direct_execution_api(app)
install_vps_direct_execution_arm_guard(app)
install_vps_direct_execution_checkpoint(app)
# Stop is a separate financial invariant. Persist its independent sentinel before
# any slower account-row lifecycle cleanup so a single Stop click forbids the next
# server BUY immediately. Successful explicit Start/Arm clears that sentinel.
install_vps_direct_hard_stop_v2(app)
# Legacy lifecycle routes remain as safe fallbacks and for explicit server-owned
# operations. They are not on the live browser proposal/BUY path. Clear Trades is
# history-only and never mutates recovery/Virtual Hook/TP/SL state.
install_vps_fast_execution_controls(app)
# Install after all direct/legacy controls. A successful fresh Start discards stale
# checkpoint/Split state; ordinary Reset leaves financial state intact. Status
# polling uses one bounded account query plus one batched preference read without
# caching financial Stop state.
install_vps_runtime_policy_hotfix(app)
# Install last so every personal mutation route, including future feature routes,
# passes through one subscription authority. Payment/setup and safe stop operations
# are explicitly exempted inside the gate. During public testing the middleware is
# still installed, but _enforcement_enabled() is false in this process.
install_premium_access_action6a(app)

app.state.production_frontend_host = "vps_frontend"
app.state.production_backend_role = "control_plane_scheduler_offline_takeover"
app.state.production_architecture = "hybrid_browser_direct_v2_global_recovery_policy"
app.state.public_testing_free_access = PUBLIC_TESTING_FREE_ACCESS
