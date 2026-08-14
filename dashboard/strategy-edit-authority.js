(() => {
  "use strict";

  if (window.__FOA_STRATEGY_EDIT_AUTHORITY__) return;
  window.__FOA_STRATEGY_EDIT_AUTHORITY__ = true;

  // dashboard-v2.js remains the canonical builder field authority. This loader
  // adds the template library, runtime UX and final edit-stability protection
  // without re-owning those canonical Builder fields.
  window.FOA_STRATEGY_EDIT_AUTHORITY_VERSION = "20260814-template-runtime-loader-v5";
  window.FOA_CANONICAL_BUILDER_EDIT_AUTHORITY = "dashboard-v2.js";

  function loadStyle(href, id) {
    if (document.getElementById(id)) return;
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  }

  function loadScript(src, id) {
    if (document.getElementById(id)) return;
    const script = document.createElement("script");
    script.id = id;
    script.src = src;
    script.defer = true;
    document.body.appendChild(script);
  }

  loadStyle("/strategy-template-library.css?v=20260814-1", "foa-template-library-css");
  loadStyle("/runtime-ux-authority.css?v=20260814-1", "foa-runtime-ux-css");
  loadScript("/strategy-template-library.js?v=20260814-2", "foa-template-library-js");
  loadScript("/runtime-ux-authority.js?v=20260814-2", "foa-runtime-ux-js");
  loadScript("/builder-edit-stability.js?v=20260814-2", "foa-builder-edit-stability-js");
})();
