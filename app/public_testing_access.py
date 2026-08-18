from __future__ import annotations

import os
from typing import Any


_FALSE_VALUES = {"0", "false", "no", "off"}


def public_testing_free_access_enabled() -> bool:
    """Return whether DerivAdmin is in the temporary free public-testing phase.

    This is intentionally independent from the future premium configuration. The
    premium/payment implementation stays installed and testable, but production
    access enforcement is bypassed while this switch is true. When the product is
    ready for paid access, set PUBLIC_TESTING_FREE_ACCESS=false and keep
    PREMIUM_ACCESS_ENFORCEMENT=true.
    """

    value = str(os.getenv("PUBLIC_TESTING_FREE_ACCESS", "true")).strip().lower()
    return value not in _FALSE_VALUES


def apply_public_testing_premium_bypass() -> bool:
    """Disable HTTP premium enforcement for the current process during testing."""

    enabled = public_testing_free_access_enabled()
    if enabled:
        # premium_access_api reads this value at request time, so setting it before
        # installing the final middleware also protects deployments whose existing
        # .env still contains PREMIUM_ACCESS_ENFORCEMENT=true.
        os.environ["PREMIUM_ACCESS_ENFORCEMENT"] = "false"
    return enabled


def apply_public_testing_scheduler_bypass() -> bool:
    """Keep scheduled sessions and active testers independent of paid expiry.

    Action 6D remains installed so its M-Pesa/renewal routes are preserved for the
    later paid launch. During public testing only, restore Action 5 as the schedule
    start authority and replace the premium expiry sweep with a no-op. This avoids
    a stale expired entitlement stopping an otherwise free tester or skipping a
    scheduled session.
    """

    if not public_testing_free_access_enabled():
        return False

    from app import automation_scheduler_action5 as scheduler
    from app import premium_renewal_action6d as renewal

    original = getattr(renewal, "_ORIGINAL_SCHEDULE_APPLY", None)
    if original is not None:
        scheduler._apply_schedule_strategy = original

    def free_testing_expiry_cycle(*, now: Any = None) -> dict[str, int]:
        del now
        return {"expired_customers": 0, "paused_accounts": 0}

    renewal.run_premium_expiry_cycle = free_testing_expiry_cycle
    return True


def install_public_testing_access_api(app: Any) -> None:
    """Expose only the non-secret access mode needed by the testing UI layer."""

    @app.get("/me/public-testing-access", include_in_schema=False)
    def public_testing_access() -> dict[str, Any]:
        enabled = public_testing_free_access_enabled()
        return {
            "public_testing_free_access": enabled,
            "premium_enforcement_active": not enabled,
        }

    app.state.public_testing_access_api_installed = True
