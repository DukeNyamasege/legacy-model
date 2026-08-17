from __future__ import annotations

"""Full-VPS production API entrypoint.

The proven backend/realtime route stack remains unchanged. Full-VPS-only session
observability, Telegram control, and constant-time dashboard session reads are
installed last so they see the final OAuth/lifecycle routes without touching the
trading worker runtime.
"""

from app.netlify_backend_api import app
from app.vps_dashboard_latency_hotfix import install_vps_dashboard_latency_hotfix
from app.vps_session_observability import install_vps_session_observability
from app.vps_telegram_control import install_vps_telegram_control


install_vps_session_observability(app)
install_vps_telegram_control(app)
install_vps_dashboard_latency_hotfix(app)

app.state.production_frontend_host = "vps_nginx"
app.state.production_backend_role = "same_origin_api_realtime"
app.state.production_architecture = "full_vps"
