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
    "real_disabled",
  ]);

  let state = null;
  let busy = false;

  function ensureControls() {
    const actions = document.querySelector(".personal-actions");
    const primary = $("btn-toggle-auto");
    if (!actions || !primary) return null;

    let notice = $("account-lifecycle-notice");
    if (!notice) {
      notice = document.createElement("div");
      notice.id = "account-lifecycle-notice";
      notice.style.cssText = [
        "display:none",
        "padding:9px 10px",
        "border:1px solid rgba(255,183,15,.28)",
        "border-radius:8px",
        "background:rgba(255,183,15,.07)",
        "color:#e8c975",
        "font-size:.66rem",
        "line-height:1.4",
      ].join(";");
      actions.prepend(notice);
    }

    let stop = $("btn-lifecycle-stop");
    if (!stop) {
      stop = document.createElement("button");
      stop.id = "btn-lifecycle-stop";
      stop.type = "button";
      stop.textContent = "Stop Auto Trading";
      stop.className = "settings-button";
      stop.style.cssText = "border-color:rgba(255,76,85,.58);color:#ffb5ba";
      primary.insertAdjacentElement("afterend", stop);
      stop.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        perform("stop");
      });
    }

    const oldStartAgain = $("btn-start-again");
    if (oldStartAgain) oldStartAgain.hidden = true;
    return { actions, primary, stop, notice };
  }

  function lifecycleFrom(me) {
    const status = String(me?.execution_status || "inactive").trim().toLowerCase();
    if (status === "stopped") return "stopped";
    if (!me?.enabled || pausedStatuses.has(status)) return "paused";
    return "running";
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

    if (lifecycle === "running") {
      controls.primary.textContent = busy ? "Pausing…" : "Pause Auto Trading";
      controls.primary.classList.remove("join");
      controls.stop.hidden = false;
      controls.notice.style.display = "none";
    } else if (lifecycle === "paused") {
      controls.primary.textContent = busy ? "Resuming…" : "Resume Auto Trading";
      controls.primary.classList.add("join");
      controls.stop.hidden = false;
      controls.notice.style.display = "block";
      controls.notice.textContent = reason || "Trading is paused. Recovery and session state are preserved.";
    } else {
      controls.primary.textContent = busy ? "Starting…" : "Start Again";
      controls.primary.classList.add("join");
      controls.stop.hidden = true;
      controls.notice.style.display = "block";
      controls.notice.textContent = reason || "Trading is stopped. The next start begins from the configured base stake.";
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
        controls.notice.textContent = error.message || "Unable to update trading state.";
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
  window.setInterval(loadMe, 15000);
})();
