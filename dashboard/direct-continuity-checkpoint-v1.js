(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_CONTINUITY_CHECKPOINT_V1__) return;
  window.__DERIVADMIN_DIRECT_CONTINUITY_CHECKPOINT_V1__ = true;

  /*
   * Browser-direct Deriv v3 deliberately has NO periodic VPS continuity writes.
   *
   * The browser is the complete live/manual financial runtime. Public ticks,
   * authenticated Deriv WebSocket, proposals, BUY, balance, contract updates and
   * recovery state stay in the browser/Deriv path. The VPS no longer takes over a
   * live browser strategy, so a 5-second financial checkpoint has no purpose and
   * only creates database/request pressure.
   *
   * Real OPEN and SETTLED trade receipts are emitted by the execution engine and
   * posted once per event to the light control plane. Stop/Clear remain separate
   * account-global control events.
   *
   * Legacy takeover checkpoint fields split_basis_debt and split_remaining_wins
   * remain named here only as migration documentation. They are browser-local
   * runtime state in v3 and are never periodically POSTed to the VPS.
   */

  function checkpoint() {
    return false;
  }

  window.DERIVADMIN_DIRECT_CONTINUITY_CHECKPOINT_V1 = Object.freeze({
    version: "20260820-browser-deriv-direct-v3-no-periodic-vps-checkpoint",
    checkpoint,
    server_writes: false,
    server_takeover: false,
    trade_receipts_only: true,
  });
})();
