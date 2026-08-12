/* Current Custom Strategy execution uses each account's own authenticated
   private WebSocket. OAuth with trade scope is the normal authorization path;
   a separately pasted PAT is retained server-side only for legacy compatibility. */
(() => {
  "use strict";

  const VERSION = "20260812-oauth-direct-account-1";

  function replaceCredentialCopy() {
    const card = document.querySelector(".credential-card");
    if (!card) return;

    const heading = card.querySelector(".panel-head h1");
    if (heading) heading.textContent = "Deriv Account Authorization";

    const pill = card.querySelector(".connection-pill");
    const connected = Boolean(card.querySelector(".connected-box"));

    if (connected) {
      if (pill) pill.textContent = "Connected";
      const box = card.querySelector(".connected-box");
      const strong = box?.querySelector("strong");
      const paragraph = box?.querySelector("p");
      if (strong) strong.textContent = "Direct account connection";
      if (paragraph) {
        paragraph.textContent =
          "Connected through Deriv OAuth. This account opens its own authenticated private WebSocket and executes only its own Custom Strategy. No separate API token is required.";
      }
    } else {
      if (pill) pill.textContent = "Reconnect";
      const form = card.querySelector("#token-form");
      if (form) {
        const replacement = document.createElement("div");
        replacement.className = "oauth-direct-box";
        replacement.innerHTML = `
          <div>
            <strong>Deriv authorization required</strong>
            <p>Reconnect this account through Deriv. Trading permission from OAuth authorizes its private WebSocket automatically; do not paste a separate API token.</p>
          </div>
          <a href="/oauth/start" data-oauth-direct-reconnect>Reconnect Deriv</a>`;
        form.replaceWith(replacement);
      }
    }

    card.dataset.oauthDirectRuntime = VERSION;
  }

  function replaceLegacyTokenErrors() {
    document.querySelectorAll(".inline-error, .inline-warning, .status-message").forEach((node) => {
      const text = String(node.textContent || "");
      if (!/trade-scope token|api token|trading credential/i.test(text)) return;
      if (/invalid|expired|rejected|connect|required|missing/i.test(text)) {
        node.textContent =
          "Deriv account authorization is unavailable. Reconnect this account through Deriv OAuth, then start Auto Trading again.";
      }
    });
  }

  function apply() {
    replaceCredentialCopy();
    replaceLegacyTokenErrors();
    if (document.body) document.body.dataset.oauthDirectRuntime = VERSION;
  }

  let scheduled = false;
  function scheduleApply() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(() => {
      scheduled = false;
      apply();
    });
  }

  const observer = new MutationObserver(scheduleApply);
  document.addEventListener(
    "DOMContentLoaded",
    () => {
      apply();
      observer.observe(document.body, { childList: true, subtree: true });
    },
    { once: true }
  );
  if (document.readyState !== "loading") {
    apply();
    if (document.body) observer.observe(document.body, { childList: true, subtree: true });
  }

  window.FOA_OAUTH_DIRECT_RUNTIME = VERSION;
})();
