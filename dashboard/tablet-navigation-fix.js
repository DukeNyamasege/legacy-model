(() => {
  "use strict";

  const TABLET_QUERY = "(min-width: 761px) and (max-width: 1024px)";

  function isTablet() {
    return window.matchMedia(TABLET_QUERY).matches;
  }

  function setTabletDrawer(open) {
    if (!isTablet()) return;
    const drawer = document.querySelector("#foa-mobile-drawer");
    if (!drawer) return;
    const next = Boolean(open);
    document.body.classList.toggle("foa-mobile-drawer-open", next);
    drawer.setAttribute("aria-hidden", next ? "false" : "true");
    document.querySelectorAll("[data-mobile-drawer-open]").forEach((button) => {
      button.setAttribute("aria-expanded", next ? "true" : "false");
    });
  }

  document.addEventListener("click", (event) => {
    if (!isTablet()) return;
    if (event.target?.closest?.("[data-mobile-drawer-open]")) {
      setTabletDrawer(true);
      return;
    }
    if (event.target?.closest?.("[data-mobile-drawer-close], [data-mobile-view]")) {
      window.setTimeout(() => setTabletDrawer(false), 0);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setTabletDrawer(false);
  });

  window.addEventListener("resize", () => {
    if (!isTablet()) {
      document.body.classList.remove("foa-mobile-drawer-open");
      return;
    }
    const drawer = document.querySelector("#foa-mobile-drawer");
    if (drawer && !document.body.classList.contains("foa-mobile-drawer-open")) {
      drawer.setAttribute("aria-hidden", "true");
    }
  });

  window.FOA_TABLET_NAVIGATION_VERSION = "20260813-1";
})();
