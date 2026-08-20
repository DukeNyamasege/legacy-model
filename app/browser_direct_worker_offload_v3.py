from __future__ import annotations

"""Final worker offload for browser-direct Deriv v3.

A live/manual account whose lifecycle is ``direct_browser`` is never a VPS trading
account in v3. The browser talks to Deriv directly for OTP, authenticated WebSocket,
proposal, BUY, balance and contract updates. Therefore a missing browser heartbeat
must NOT cause the persistent worker to create a private Deriv session or execute a
server takeover.

Scheduled/server-owned accounts are intentionally unaffected.
"""

import logging
from typing import Any

from app import account_mode_execution_lock as mode_lock
from app import browser_direct_lease_preservation_authority as lease_preservation
from app import direct_browser_runtime_authority as browser_runtime
from app import direct_execution_worker_fence as worker_fence
from app.direct_execution_lease import DIRECT_BROWSER_STATUS


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False


def _browser_direct_owned(row: Any) -> bool:
    return bool(
        row is not None
        and bool(getattr(row, "enabled", False))
        and str(getattr(row, "execution_status", "") or "").strip().lower()
        == DIRECT_BROWSER_STATUS
    )


def install_browser_direct_worker_offload_v3() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # These modules imported direct_browser_lease_fresh by value. Replace their
    # module globals so direct_browser means browser-owned for the complete active
    # run, not only while a short server lease timestamp is fresh.
    mode_lock.direct_browser_lease_fresh = _browser_direct_owned
    browser_runtime.direct_browser_lease_fresh = _browser_direct_owned
    lease_preservation.direct_browser_lease_fresh = _browser_direct_owned
    worker_fence.direct_browser_lease_fresh = _browser_direct_owned

    # Defense in depth: the old 2-second lease scanner is retired for live/manual
    # browser execution. It cannot promote direct_browser rows to connecting and
    # therefore cannot start VPS OTP/private-WebSocket/provider repair storms.
    def no_browser_takeover(_bot: Any) -> list[int]:
        return []

    worker_fence._promote_expired_browser_leases = no_browser_takeover

    # Public markers used by release tests and production diagnostics.
    mode_lock.account_allows_new_execution._browser_direct_v3 = True  # type: ignore[attr-defined]
    browser_runtime._browser_direct_worker_offload_v3 = True
    worker_fence._browser_direct_worker_offload_v3 = True
    worker_fence._browser_direct_takeover_enabled = False

    LOGGER.warning(
        "BROWSER_DIRECT_WORKER_OFFLOAD_V3_INSTALLED provider_requests=false "
        "browser_direct_takeover=false server_trade_transport=false "
        "scheduled_server_execution_preserved=true"
    )
    _INSTALLED = True
