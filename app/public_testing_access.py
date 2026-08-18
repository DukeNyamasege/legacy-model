from __future__ import annotations

import os


_FALSE_VALUES = {"0", "false", "no", "off"}


def public_testing_free_access_enabled() -> bool:
    """Return whether DerivAdmin is in the temporary free public-testing phase.

    This is intentionally independent from the future premium configuration.  The
    premium/payment implementation stays installed and testable, but production
    access enforcement is bypassed while this switch is true.  When the product is
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
