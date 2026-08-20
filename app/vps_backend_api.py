from __future__ import annotations

"""Full-VPS production API entrypoint.

Live/manual Options transport is browser-direct Deriv v3. The VPS serves the site,
OAuth/session control, strategy settings, Stop/Clear synchronization, scheduling,
and lightweight OPEN/SETTLED trade receipts. It is not the live tick/OTP/proposal/
BUY/private-WebSocket transport.
"""

from app.vps_core_api import app
from app.automation_preferences_api import install_automation_preferences_api
from app.automation_scheduler_action5 import install_automation_scheduler_action5
from app.automation_scheduler_v2_authority import install_automation_scheduler_v2_authority
from app.final_linked_accounts_6f2 import install_final_linked_accounts_6f2
from app.lipana_mpesa_action6b import install_lipana_mpesa_action6b
from app.marketing_tutorial_account import install_marketing_tutorial_account
from app.premium_access_api import install_premium_access_action6a
from app.premium_renewal_action6d import install_premium_renewal_action6d
from app.public_testing_access import (
    apply_public_testing_premium_bypass,
    apply_public_testing_scheduler_bypass,
    install_public_testing_access_api,
)
from app.text_to_strategy_api import install_text_to_strategy_api
from app.vps_cross_device_runtime_sync import install_vps_cross_device_runtime_sync
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


PUBLIC_TESTING_FREE_ACCESS = apply_public_testing_premium_bypass()

install_vps_session_observability(app)
install_vps_login_observability_hotfix()
install_vps_telegram_control(app)
install_vps_dashboard_latency_hotfix(app)
install_text_to_strategy_api(app)
install_automation_preferences_api(app)
# Scheduling remains server-owned because it must run when no browser is open.
install_automation_scheduler_action5(app)
install_automation_scheduler_v2_authority()
install_lipana_mpesa_action6b(app)
install_premium_renewal_action6d(app)
apply_public_testing_scheduler_bypass()
install_public_testing_access_api(app)
install_final_linked_accounts_6f2(app)
install_vps_linked_accounts_latency_hotfix(app)
# Account management remains a low-frequency control-plane operation.
install_vps_demo_balance_reset(app)
# Compatibility quotas and old direct routes install first. Browser-direct v3,
# installed by the final cross-device authority below, removes live server OTP,
# heartbeat and takeover traffic while preserving safe old-client boundaries.
install_vps_direct_runtime_rate_limit()
install_vps_direct_execution_api(app)
install_vps_direct_execution_arm_guard(app)
install_vps_direct_execution_checkpoint(app)
# Stop remains account-global and durable before any slower UI persistence.
install_vps_direct_hard_stop_v2(app)
install_vps_fast_execution_controls(app)
install_vps_runtime_policy_hotfix(app)
# This installs cross-device Stop/Clear, then browser-direct Deriv v3 absolutely
# last for live/manual transport: browser -> Deriv for OTP/WSS/proposal/BUY;
# VPS -> control/settings plus OPEN/SETTLED receipts only.
install_vps_cross_device_runtime_sync(app)
# The marketing tutorial wrapper is intentionally after the final browser-direct
# routes. It can present ROT92069206 while forcing the provider bootstrap, arm and
# receipts to remain on DOT93427967 demo. Every ordinary account delegates to the
# already-final fast production endpoints unchanged.
install_marketing_tutorial_account(app)
# Premium/payment middleware is still the final access-control layer.
install_premium_access_action6a(app)

app.state.production_frontend_host = "vps_frontend"
app.state.production_backend_role = "light_control_scheduler_trade_receipts"
app.state.production_architecture = "browser_deriv_direct_v3"
app.state.live_manual_provider_path = "browser_to_deriv"
app.state.live_manual_server_otp = False
app.state.live_manual_server_websocket = False
app.state.live_manual_server_proposal = False
app.state.live_manual_server_buy = False
app.state.live_manual_server_takeover = False
app.state.public_testing_free_access = PUBLIC_TESTING_FREE_ACCESS
