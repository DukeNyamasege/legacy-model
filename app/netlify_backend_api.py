from __future__ import annotations

"""Backend-only production API for the Netlify + dedicated VPS architecture.

`app.api_v3` is imported first so every existing account/OAuth/Custom Strategy
compatibility layer finishes installing. The split-architecture gateway is then
installed last and owns only the backend realtime/control boundary.
"""

from app.api_v3 import app
from app.netlify_realtime_gateway import install_netlify_realtime_gateway


install_netlify_realtime_gateway(app)

app.state.production_frontend_host = "netlify"
app.state.production_backend_role = "api_only"
