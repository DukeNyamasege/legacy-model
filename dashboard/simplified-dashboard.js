(() => {
  "use strict";

  // Compatibility route only. The production UI is the builder-first dashboard.
  if (window.FOA_BUILDER_FIRST_COMPAT_LOADED) return;
  window.FOA_BUILDER_FIRST_COMPAT_LOADED = true;

  if (window.FOA_BUILDER_FIRST_APP) return;

  const script = document.createElement("script");
  script.src = "/ui/dashboard-v2.js";
  script.defer = true;
  document.head.appendChild(script);
})();
