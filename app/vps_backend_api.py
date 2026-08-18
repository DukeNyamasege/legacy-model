from __future__ import annotations

"""Full-VPS production API entrypoint.

The proven backend/realtime route stack is imported first so every compatibility
layer finishes installing before Full-VPS-only observability, Telegram control,
preferences, the persistent Action 5 scheduler, and the Action 6 premium/payment
layers wrap the final routes/lifespan. None of these replaces the financial worker.
"""

from app.netlify_backend_api import app
from app.automation_preferences_api import install_automation_preferences_api
from app.automation_scheduler_action5 import install_automation_scheduler_action5
from app.final_linked_accounts_6f2 import install_final_linked_accounts_6f2
from app.lipana_mpesa_action6b import install_lipana_mpesa_action6b
from app.premium_access_api import install_premium_access_action6a
from app.premium_renewal_action6d import install_premium_renewal_action6d
from app.public_testing_access import (
    apply_public_testing_premium_bypass,
    apply_public_testing_scheduler_bypass,
)
from app.text_to_strategy_api import install_text_to_strategy_api
from app.vps_dashboard_latency_hotfix import install_vps_dashboard_latency_hotfix
from app.vps_login_observability_hotfix import install_vps_login_observability_hotfix
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
install_automation_scheduler_action5(app)
install_lipana_mpesa_action6b(app)
install_premium_renewal_action6d(app)
# Action 6D keeps its payment/renewal routes, but while testing is free it may not
# skip scheduled starts or pause accounts just because an old premium period ended.
apply_public_testing_scheduler_bypass()
# 6F-2 replaces only the account selector route. It changes the selected
# ClientSession account and never starts/stops trading or changes strategy/risk.
install_final_linked_accounts_6f2(app)
# Install last so every personal mutation route, including future feature routes,
# passes through one subscription authority. Payment/setup and safe stop operations
# are explicitly exempted inside the gate. During public testing the middleware is
# still installed, but _enforcement_enabled() is false in this process.
install_premium_access_action6a(app)

app.state.production_frontend_host = "vps_nginx"
app.state.production_backend_role = "same_origin_api_realtime"
app.state.production_architecture = "full_vps"
app.state.public_testing_free_access = PUBLIC_TESTING_FREE_ACCESS
