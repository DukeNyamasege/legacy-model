from __future__ import annotations

"""Full-VPS production API entrypoint.

The proven backend/realtime route stack is imported first so every compatibility
layer finishes installing before Full-VPS-only observability, Telegram control,
preferences, and the persistent Action 5 scheduler wrap the final routes/lifespan.
The scheduler never replaces the financial execution worker.
"""

from app.netlify_backend_api import app
from app.automation_preferences_api import install_automation_preferences_api
from app.automation_scheduler_action5 import install_automation_scheduler_action5
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

app.state.production_frontend_host = "vps_nginx"
app.state.production_backend_role = "same_origin_api_realtime"
app.state.production_architecture = "full_vps"
