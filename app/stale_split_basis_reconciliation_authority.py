from __future__ import annotations

"""Final stale-state repair before Split recovery stake sizing.

If all configured Split successes are still outstanding, no successful recovery leg
has consumed the current loss pool yet. A legacy `remaining=0` while real debt
still exists is also an impossible completed-cycle marker and is treated as a new
unconsumed Split-N recovery cycle. In either case the persistent basis debt must
match the authoritative AccountRiskState debt and the fixed part stake is repriced
once from the next valid proposal.
"""

import logging
from typing import Any

from app import custom_split_equal_spread_authority as equal_split
from app import global_recovery_execution_policy as global_policy
from app import manual_martingale_v2 as manual
from app.repositories.rf_dir5_repository import RFDir5Repository, VIRTUAL_WAITING_FOR_WIN


LOGGER = logging.getLogger("deriv_bot")
_INSTALLED = False
_ORIGINAL_PLAN_STAKE: Any = None


def install_stale_split_basis_reconciliation_authority() -> None:
    global _INSTALLED, _ORIGINAL_PLAN_STAKE
    if _INSTALLED:
        return

    _ORIGINAL_PLAN_STAKE = RFDir5Repository.plan_stake

    def plan_stake(self: RFDir5Repository, *args: Any, **kwargs: Any):
        original = _ORIGINAL_PLAN_STAKE
        if original is None:
            raise RuntimeError("Final recovery stake planner is unavailable")
        try:
            managed_id = int(kwargs.get("managed_account_id"))
            settings = manual.read_manual_martingale_settings(self, managed_id)
            family = manual._manual_family(self, managed_id)
            if family != "system" and str(settings.get("mode") or "") == manual.SPLIT_MODE:
                snapshot = manual._account_snapshot(self, managed_id)
                debt = max(0.0, float(snapshot.get("debt") or 0.0))
                mode = str(snapshot.get("mode") or "")
                split_count = max(1, min(3, int(settings.get("split_count") or 1)))
                remaining = manual._read_split_remaining(self, managed_id)
                basis = equal_split._read_basis_debt(self, managed_id)
                unconsumed_cycle = remaining <= 0 or remaining == split_count
                basis_mismatch = basis <= 0.009 or abs(basis - debt) > 0.009
                if (
                    debt > 0.009
                    and mode != VIRTUAL_WAITING_FOR_WIN
                    and unconsumed_cycle
                    and (remaining <= 0 or basis_mismatch)
                ):
                    old_remaining = remaining
                    old_basis = basis
                    manual._write_split_remaining(self, managed_id, split_count)
                    equal_split._write_basis_debt(self, managed_id, debt)
                    global_policy._write_part_stake(self, managed_id, 0.0)
                    LOGGER.warning(
                        "GLOBAL_SPLIT_STALE_BASIS_REPAIRED managed_id=%s old_basis=%.2f "
                        "current_debt=%.2f old_remaining=%s remaining_successes=%s "
                        "split_count=%s fixed_part_stake_reset=true recovery=true",
                        managed_id,
                        old_basis,
                        debt,
                        old_remaining,
                        split_count,
                        split_count,
                    )
        except Exception:
            # Stale-state reconciliation is protective. The authoritative final
            # recovery planner below still decides the trade even if inspection
            # itself encounters a legacy preference parse problem.
            pass
        return original(self, *args, **kwargs)

    RFDir5Repository.plan_stake = plan_stake
    RFDir5Repository._stale_split_basis_reconciliation_installed = True
    _INSTALLED = True

    # Final lifecycle order:
    # 1. TP/SL/manual-only authority prevents automatic terminal stops.
    # 2. Browser lease preservation prevents transient worker/provider status
    #    mutations from stealing a still-live browser execution lease.
    # TP, SL and the durable explicit-user hard-stop sentinel still pass through.
    from app.tp_sl_manual_only_authority import install_tp_sl_manual_only_authority
    from app.browser_direct_lease_preservation_authority import (
        install_browser_direct_lease_preservation_authority,
    )

    install_tp_sl_manual_only_authority()
    install_browser_direct_lease_preservation_authority()
