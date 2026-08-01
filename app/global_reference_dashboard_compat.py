from __future__ import annotations

from typing import Any

from app.repositories.test2_repository import Test2Repository

_INSTALLED = False
_ORIGINAL_SYSTEM_PERFORMANCE_SUMMARY = None


def install_global_reference_dashboard_compat() -> None:
    """Keep the $0.50 reference model compatible with older dashboard callers.

    Some dashboard-consistency paths call repository.system_performance_summary
    with legacy keyword arguments such as base_stake and observed_executions.
    The new reference replay does not need those values, but it must accept them
    so background refreshes cannot crash the API and trigger a 502 page.
    """

    global _INSTALLED, _ORIGINAL_SYSTEM_PERFORMANCE_SUMMARY
    if _INSTALLED:
        return

    original = Test2Repository.system_performance_summary
    _ORIGINAL_SYSTEM_PERFORMANCE_SUMMARY = original

    def compatible_system_performance_summary(
        self: Test2Repository,
        *,
        start,
        end,
        simulated_base_stake: float | None = None,
        base_stake: float | None = None,
        include_virtual: bool = False,
        trades: list[dict[str, Any]] | None = None,
        **_legacy_kwargs: Any,
    ) -> dict[str, Any]:
        reference_stake = (
            simulated_base_stake
            if simulated_base_stake is not None
            else base_stake
            if base_stake is not None
            else 0.50
        )
        return original(
            self,
            start=start,
            end=end,
            simulated_base_stake=float(reference_stake or 0.50),
            include_virtual=include_virtual,
            trades=trades,
        )

    compatible_system_performance_summary._global_reference_compat_installed = True  # type: ignore[attr-defined]
    Test2Repository.system_performance_summary = compatible_system_performance_summary
    Test2Repository._global_reference_dashboard_compat_installed = True
    _INSTALLED = True
