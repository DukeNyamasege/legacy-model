from __future__ import annotations

"""VPS-native core API bootstrap.

The entire production stack is hosted on the VPS. ``app.api_v3`` installs the
existing OAuth, account, Custom Strategy and compatibility routes first; the
VPS-only realtime and API surfaces are then installed on top of that final app.
"""

from app.api_v3 import app
from app.final_trade_history_cutoff_authority import (
    install_final_trade_history_cutoff_authority,
)
from app.seamless_account_switch import install_seamless_account_switch
from app.vps_api_surface import install_vps_api_surface
from app.vps_realtime_gateway import install_vps_realtime_gateway


# Install after api_v3 so these wrappers see the final account-switch and /me
# authorities rather than an older compatibility route.
install_seamless_account_switch(app)
install_vps_realtime_gateway(app)
install_vps_api_surface(app)

# Install last. The performance API intentionally transports only a bounded recent
# row window, but Clear Trades and KPI totals are account-global and unlimited.
# This authority applies the durable server cutoff to realtime/REST summaries and
# wakes connected dashboards immediately after a clear.
install_final_trade_history_cutoff_authority(app)

app.state.production_frontend_host = "vps_frontend"
app.state.production_backend_role = "vps_api"
app.state.production_hosting = "vps_only"
