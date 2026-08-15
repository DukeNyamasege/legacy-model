from __future__ import annotations

"""Full-VPS production API entrypoint.

The proven backend/realtime route stack remains unchanged; this wrapper only marks
that the public frontend, REST, OAuth and realtime edge are now hosted by Nginx on
the same VPS instead of Netlify.
"""

from app.netlify_backend_api import app


app.state.production_frontend_host = "vps_nginx"
app.state.production_backend_role = "same_origin_api_realtime"
app.state.production_architecture = "full_vps"
