from __future__ import annotations

"""Final Full-VPS private-connection continuity policy.

The shared Deriv request broker already has bounded keep-alive HTTP, safe OTP
retries and provider-rate-limit handling. The low-latency layer previously put an
8-second outer timeout around that broker, cancelling legitimate in-flight OTP
work before the broker's own bounded timeout/retry policy could finish. Under a
multi-account reconnect this produced endless OTP timeout churn.

This authority keeps provider rate-limit backoff intact, gives the broker enough
time to complete, lowers ordinary OTP bootstrap concurrency, and keeps urgent
browser->VPS takeover sessions ahead of background reconnects.
"""

import logging
import os
from typing import Any

from app import vps_low_latency_runtime as low


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False

OTP_BOOTSTRAP_TIMEOUT_SECONDS = 45.0
BOOTSTRAP_CONCURRENCY = 3
OTP_HTTP_CONCURRENCY = 4
TAKEOVER_PRIORITY_SECONDS = 90.0


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def install_vps_provider_connection_resilience_v2() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Let the pooled broker own the real HTTP timeout/retry boundary. Forty-five
    # seconds covers two broker attempts (20s each + bounded retry delay) without
    # creating an unbounded connection wait.
    low._OTP_BOOTSTRAP_TIMEOUT_SECONDS = OTP_BOOTSTRAP_TIMEOUT_SECONDS
    low._CONTRACT_SNAPSHOT_OTP_TIMEOUT_SECONDS = OTP_BOOTSTRAP_TIMEOUT_SECONDS
    low._PRIORITY_SECONDS = TAKEOVER_PRIORITY_SECONDS

    configured_http = _bounded_env_int(
        "VPS_OTP_HTTP_CONCURRENCY",
        OTP_HTTP_CONCURRENCY,
        2,
        OTP_HTTP_CONCURRENCY,
    )
    os.environ["VPS_OTP_HTTP_CONCURRENCY"] = str(configured_http)

    def scheduler_for(session: Any):
        bot = session.bot
        scheduler = getattr(bot, "_vps_private_bootstrap_scheduler", None)
        if isinstance(scheduler, low._VpsBootstrapScheduler):
            return scheduler
        configured = _bounded_env_int(
            "VPS_PRIVATE_WS_BOOTSTRAP_CONCURRENCY",
            BOOTSTRAP_CONCURRENCY,
            1,
            BOOTSTRAP_CONCURRENCY,
        )
        scheduler = low._VpsBootstrapScheduler(configured)
        bot._vps_private_bootstrap_scheduler = scheduler
        bot.logger.warning(
            "PRIVATE_WS_BOOTSTRAP_RESILIENCE_V2 concurrency=%s "
            "otp_timeout_seconds=%.1f otp_http_concurrency=%s "
            "urgent_takeover_priority_seconds=%.1f broker_retry_boundary=authoritative",
            scheduler.limit,
            OTP_BOOTSTRAP_TIMEOUT_SECONDS,
            configured_http,
            TAKEOVER_PRIORITY_SECONDS,
        )
        return scheduler

    low._scheduler_for = scheduler_for
    low._vps_provider_connection_resilience_v2_installed = True
    _INSTALLED = True
    LOGGER.warning(
        "VPS_PROVIDER_CONNECTION_RESILIENCE_V2_INSTALLED "
        "outer_otp_cancel_storm=false global_validation_on_takeover=false"
    )
