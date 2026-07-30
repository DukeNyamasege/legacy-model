(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.error || `Request failed (${response.status})`);
    return body;
  };

  const pausedStatuses = new Set([
    "manual_pause",
    "take_profit",
    "stop_loss",
    "insufficient_balance",
    "purchase_insufficient_balance",
    "credential_error",
    "invalid_account",
    "token_required",
    "bulk_execution_pat_required",
    "contract_unavailable",
    "purchase_error",
    "real_disabled",
  ]);

  let state = null;
  let busy = false;

  function ensureControls() {
    // Support both dashboard generations. The compact/previous UI uses
    // #auto-trade-panel; the newer UI used .personal-actions.
    const actions = document.querySelector(".personal-actions") || $("auto-trade-panel");
    const primary = $("btn-toggle-auto");
    if (!actions || !primary) return null;

    const legacyResume = $("resume-panel");
    if (legacyResume) legacyResume.style.display = "none";
    const oldStartAgain = $("btn-start-again");
    if (oldStartAgain) oldStartAgain.hidden = true;

    let notice = $("account-lifecycle-notice");
    if (!notice) {
      notice = document.createElement("div");
      notice.id = "account-lifecycle-notice";
      notice.setAttribute("role", "status");
      notice.setAttribute("aria-live", "polite");
      notice.style.cssText = [
        "display:none",
        "max-width:320px",
        "margin-bottom:9px",
        "padding:10px 11px",
        "border:1px solid rgba(255,183,15,.32)",
        "border-radius:9px",
        "background:rgba(255,183,15,.08)",
        "color:#f2d98a",
        "font-size:.72rem",
        "line-height:1.45",
        "text-align:left",
      ].join(";");
      actions.prepend(notice);
    }

    let stop = $("btn-lifecycle-stop");
    if (!stop) {
      stop = document.createElement("button");
      stop.id = "btn-lifecycle-stop";
      stop.type = "button";
      stop.textContent = "Stop Auto Trading";
      stop.className = "stop";
      stop.style.cssText = [
        "display:none",
        "min-height:44px",
        "margin-top:8px",
        "padding:0 15px",
        "border:1px solid rgba(255,76,85,.72)",
        "border-radius:12px",
        "background:rgba(150,25,35,.2)",
        "color:#ffc0c4",
        "font-weight:700",
        "cursor:pointer",
      ].join(";");
      primary.insertAdjacentElement("afterend", stop);
      stop.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        perform("stop");
      });
    }

    return { actions, primary, stop, notice };
  }

  function lifecycleFrom(me) {
    const status = String(me?.execution_status || "inactive").trim().toLowerCase();
    if (status === "stopped") return "stopped";
    if (!me?.enabled || pausedStatuses.has(status)) return "paused";
    return "running";
  }

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function render(me) {
    state = me;
    const controls = ensureControls();
    if (!controls || !me?.authenticated) return;

    const status = String(me.execution_status || "inactive").trim().toLowerCase();
    const lifecycle = lifecycleFrom(me);
    const reason = String(me.execution_status_reason || "").trim();
    const hasToken = Boolean(me.has_trading_api_token) && !Boolean(me.trading_api_token_invalid);

    controls.primary.disabled = busy || (!hasToken && lifecycle !== "running");
    controls.stop.disabled = busy;
    controls.primary.style.display = "inline-flex";

    if (lifecycle === "running") {
      setText(controls.primary, busy ? "Pausing…" : "Pause Auto Trading");
      controls.primary.classList.remove("join");
      controls.primary.classList.add("stop");
      // Keep only one visible auto-trading action in the compact dashboard.
      // Showing both Pause and Stop side-by-side made the labels read as one
      // broken string on narrow layouts and confused the current account state.
      controls.stop.style.display = "none";

      // Running does not always mean "nothing to report". A small account can
      // remain joined while an oversized recovery stake is deliberately skipped,
      // or while a private connection is recovering. Surface that state here.
      const informative = Boolean(reason && !["active", "connecting", "validating"].includes(status));
      controls.notice.style.display = informative ? "block" : "none";
      if (informative) setText(controls.notice, reason);
    } else if (lifecycle === "paused") {
      setText(controls.primary, busy ? "Resuming…" : "Resume Auto Trading");
      controls.primary.classList.remove("stop");
      controls.primary.classList.add("join");
      controls.stop.style.display = "none";
      controls.notice.style.display = "block";
      setText(
        controls.notice,
        reason || "Trading is paused. Recovery and session state are preserved."
      );
    } else {
      setText(controls.primary, busy ? "Starting…" : "Start Trading");
      controls.primary.classList.remove("stop");
      controls.primary.classList.add("join");
      controls.stop.style.display = "none";
      controls.notice.style.display = "block";
      setText(
        controls.notice,
        reason || "Trading is stopped. Starting again begins from your configured base stake."
      );
    }

    controls.primary.dataset.lifecycle = lifecycle;
    controls.primary.title = !hasToken && lifecycle !== "running"
      ? "Fix the trading API token in Settings before resuming."
      : "";
  }

  async function loadMe() {
    try {
      const me = await api("/me");
      render(me);
    } catch (_) {}
  }

  async function perform(action) {
    if (busy || !state?.authenticated) return;
    busy = true;
    render(state);
    try {
      if (action === "pause") {
        await api("/me/pause-trading", { method: "POST" });
      } else if (action === "stop") {
        await api("/me/stop-trading", { method: "POST" });
      } else if (action === "resume") {
        await api("/me/resume-trading", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "continue" }),
        });
      } else if (action === "start") {
        await api("/me/resume-trading", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "start_again" }),
        });
      }
      await loadMe();
    } catch (error) {
      const controls = ensureControls();
      if (controls) {
        controls.notice.style.display = "block";
        setText(controls.notice, error.message || "Unable to update trading state.");
      }
    } finally {
      busy = false;
      await loadMe();
    }
  }

  function bindPrimary() {
    const primary = $("btn-toggle-auto");
    if (!primary || primary.dataset.lifecyclePatchBound === "1") return;
    primary.dataset.lifecyclePatchBound = "1";
    primary.addEventListener("click", event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const lifecycle = primary.dataset.lifecycle || lifecycleFrom(state);
      if (lifecycle === "running") perform("pause");
      else if (lifecycle === "paused") perform("resume");
      else perform("start");
    }, true);
  }

  const observer = new MutationObserver(() => {
    ensureControls();
    bindPrimary();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  ensureControls();
  bindPrimary();
  loadMe();

  // The restored compact dashboard has its own legacy button renderer. Re-apply
  // lifecycle labels locally so a 30-second dashboard refresh cannot turn
  // "Pause" back into the old misleading "Stop/Join" label.
  window.setInterval(() => {
    if (state?.authenticated) render(state);
  }, 1000);
  window.setInterval(loadMe, 7000);
})();
