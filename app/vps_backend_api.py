from __future__ import annotations

"""Full-VPS production API entrypoint.

The proven backend/realtime route stack remains unchanged. Full-VPS-only session
observability is installed last so it sees the final OAuth and lifecycle routes.
"""

from app.netlify_backend_api import app
from app.vps_session_observability import install_vps_session_observability


install_vps_session_observability(app)

app.state.production_frontend_host = "vps_nginx"
app.state.production_backend_role = "same_origin_api_realtime"
app.state.production_architecture = "full_vps"
