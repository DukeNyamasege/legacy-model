from __future__ import annotations

"""Full-VPS production API entrypoint.

The proven backend/realtime route stack remains unchanged; this wrapper marks the
public frontend, REST, OAuth and realtime edge as same-origin VPS services and
installs the lightweight live strategy-progress fanout used by the VPS dashboard.
"""

from app.netlify_backend_api import app
from app.vps_realtime_events import install_vps_realtime_events


install_vps_realtime_events(app)

app.state.production_frontend_host = "vps_nginx"
app.state.production_backend_role = "same_origin_api_realtime"
app.state.production_architecture = "full_vps"
