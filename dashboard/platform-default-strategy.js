(() => {
  "use strict";

  /*
   * The platform default is now owned by strategy-template-library.js so Reset,
   * first-time accounts, built-in presets and user-saved local templates all use
   * one authority. Keeping this tiny compatibility marker prevents older cached
   * UI layers from re-installing the former Differs preset.
   */
  if (window.__FOA_PLATFORM_DEFAULT_STRATEGY__) return;
  window.__FOA_PLATFORM_DEFAULT_STRATEGY__ = true;
  window.__FOA_PLATFORM_DEFAULT_DELEGATED_TO_TEMPLATES__ = true;
  window.FOA_PLATFORM_DEFAULT_STRATEGY_VERSION = "20260814-golden-template-default";
})();
