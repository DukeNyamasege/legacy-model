from __future__ import annotations

"""Backend-only production API for the Netlify + dedicated VPS architecture.

`app.api_v3` is imported first so every existing account/OAuth/Custom Strategy
compatibility layer finishes installing. The split-architecture gateway and final
backend-only surface are then installed last.
"""

from app.api_v3 import app
from app.backend_only_surface import install_backend_only_surface
from app.netlify_realtime_gateway import install_netlify_realtime_gateway


install_netlify_realtime_gateway(app)
install_backend_only_surface(app)

app.state.production_frontend_host = "netlify"
app.state.production_backend_role = "api_only"
