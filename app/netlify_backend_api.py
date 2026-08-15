from __future__ import annotations

"""Backend-only production API for the Netlify + dedicated VPS architecture.

`app.api_v3` is imported first so every existing account/OAuth/Custom Strategy
compatibility layer finishes installing. The split-architecture gateway and final
backend-only surface are then installed last.
"""

from app.api_v3 import app
from app.backend_only_surface import install_backend_only_surface
from app.final_trade_history_cutoff_authority import (
    install_final_trade_history_cutoff_authority,
)
from app.netlify_realtime_gateway import install_netlify_realtime_gateway
from app.seamless_account_switch import install_seamless_account_switch


# Install after api_v3 so this wrapper calls the final account-switch and /me
# authorities rather than an older compatibility route.
install_seamless_account_switch(app)
install_netlify_realtime_gateway(app)
install_backend_only_surface(app)

# Install last. The performance API intentionally transports only a bounded recent
# row window, but Clear Trades and the KPI totals are account-global and unlimited.
# This final authority applies the durable server cutoff to realtime/REST summaries
# and wakes every connected dashboard immediately after a clear.
install_final_trade_history_cutoff_authority(app)

app.state.production_frontend_host = "netlify"
app.state.production_backend_role = "api_only"
