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
from app.lipana_mpesa_action6b import install_lipana_mpesa_action6b
from app.premium_access_api import install_premium_access_action6a
from app.text_to_strategy_api import install_text_to_strategy_api
from app.vps_dashboard_latency_hotfix import install_vps_dashboard_latency_hotfix
from app.vps_login_observability_hotfix import install_vps_login_observability_hotfix
from app.vps_session_observability import install_vps_session_observability
from app.vps_telegram_control import install_vps_telegram_control


install_vps_session_observability(app)
install_vps_login_observability_hotfix()
install_vps_telegram_control(app)
install_vps_dashboard_latency_hotfix(app)
install_text_to_strategy_api(app)
install_automation_preferences_api(app)
install_automation_scheduler_action5(app)
install_lipana_mpesa_action6b(app)
# Install last so every personal mutation route, including future feature routes,
# passes through one subscription authority. Payment/setup and safe stop operations
# are explicitly exempted inside the gate.
install_premium_access_action6a(app)

app.state.production_frontend_host = "vps_nginx"
app.state.production_backend_role = "same_origin_api_realtime"
app.state.production_architecture = "full_vps"
