from __future__ import annotations

"""Full-VPS production API entrypoint.

The proven backend/realtime route stack remains unchanged. Full-VPS-only session
observability and the single Telegram polling/control plane are installed last so
they see the final OAuth and lifecycle routes without competing getUpdates loops.
"""

from app.netlify_backend_api import app
from app.vps_session_observability import install_vps_session_observability
from app.vps_telegram_control import install_vps_telegram_control


install_vps_session_observability(app)
install_vps_telegram_control(app)

app.state.production_frontend_host = "vps_nginx"
app.state.production_backend_role = "same_origin_api_realtime"
app.state.production_architecture = "full_vps"
