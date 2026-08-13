(() => {
  "use strict";

  if (window.__FOA_STRATEGY_EDIT_AUTHORITY__) return;
  window.__FOA_STRATEGY_EDIT_AUTHORITY__ = true;

  // Compatibility no-op. dashboard-v2.js is the only authority for the main
  // builder controls. Result-Based Trading keeps its own scoped draft handler.
  window.FOA_STRATEGY_EDIT_AUTHORITY_VERSION = "20260813-disabled-3";
  window.FOA_CANONICAL_BUILDER_EDIT_AUTHORITY = "dashboard-v2.js";
})();
