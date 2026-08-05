(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const REQUIRED_PAT_MESSAGE = "Action required: Link your Deriv API token with trade scope in Settings > Credentials. How to get it: open Deriv, go to Security & limits, open API token, create a token with trade permission, then paste it here.";
  const INVALID_PAT_MESSAGE = "Your Deriv API token has expired or is invalid. Go to Settings > Credentials and add a new token with trade permission.";

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

  const stoppedStatuses = new Set([
    "inactive",
    "disabled",
    "stopped",
  ]);

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
    const actions = document.querySelector(".personal-actions") || $("auto-trade-panel");
    const primary = $("btn-toggle-auto");
    if (!actions || !primary) return null;

    const legacyResume = $("resume-panel");
    if (legacyResume) legacyResume.style.display = "none";
    const oldStartAgain = $("btn-start-again");
    if (oldStartAgain) oldStartAgain.hidden = true;
    const oldStop = $("btn-lifecycle-stop");
    if (oldStop) oldStop.style.display = "none";

    let notice = $("account-lifecycle-notice");
    if (!notice) {
      notice = document.createElement("div");
      notice.id = "account-lifecycle-notice";
      notice.setAttribute("role", "status");
      notice.setAttribute("aria-live", "polite");
      notice.style.cssText = [
        "display:none",
        "max-width:340px",
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

    let pauseResume = $("btn-lifecycle-pause-resume");
    if (!pauseResume) {
      pauseResume = document.createElement("button");
      pauseResume.id = "btn-lifecycle-pause-resume";
      pauseResume.type = "button";
      pauseResume.textContent = "Pause Auto Trading";
      pauseResume.className = "join";
      pauseResume.style.cssText = [
        "display:none",
        "min-height:44px",
        "margin-top:8px",
        "padding:0 15px",
        "border:1px solid rgba(49,143,255,.65)",
        "border-radius:12px",
        "background:rgba(49,143,255,.12)",
        "color:#d6e9ff",
        "font-weight:700",
        "cursor:pointer",
      ].join(";");
      primary.insertAdjacentElement("afterend", pauseResume);
      pauseResume.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        const lifecycle = pauseResume.dataset.lifecycle || lifecycleFrom(state);
        if (lifecycle === "running") perform("pause");
        else if (lifecycle === "paused") perform("resume");
      });
    }

    return { actions, primary, pauseResume, notice };
  }

  function lifecycleFrom(me) {
    const status = String(me?.execution_status || "inactive").trim().toLowerCase();
    if (stoppedStatuses.has(status)) return "stopped";
    if (!me?.enabled && !pausedStatuses.has(status)) return "stopped";
    if (!me?.enabled || pausedStatuses.has(status)) return "paused";
    return "running";
  }

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function tokenNoticeFrom(me, reason) {
    const status = String(me?.execution_status || "").trim().toLowerCase();
    const invalid = Boolean(me?.trading_api_token_invalid) || status === "credential_error";
    const missing = Boolean(me?.requires_api_token) || status === "token_required" || status === "bulk_execution_pat_required";
    if (invalid) return reason || INVALID_PAT_MESSAGE;
    if (missing || !Boolean(me?.has_trading_api_token)) return reason || REQUIRED_PAT_MESSAGE;
    return "";
  }

  function render(me) {
    state = me;
    const controls = ensureControls();
    if (!controls || !me?.authenticated) return;

    const status = String(me.execution_status || "inactive").trim().toLowerCase();
    const lifecycle = lifecycleFrom(me);
    const reason = String(me.execution_status_reason || "").trim();
    const tokenNotice = tokenNoticeFrom(me, reason);
    const hasToken = !tokenNotice;
    const modeLabel = String(me.account_type || "account").toUpperCase();

    controls.primary.style.display = "inline-flex";
    controls.primary.dataset.lifecycle = lifecycle;
    controls.pauseResume.dataset.lifecycle = lifecycle;
    controls.primary.disabled = busy || !hasToken || (lifecycle === "stopped" && !hasToken);
    controls.pauseResume.disabled = busy || !hasToken || (lifecycle === "paused" && !hasToken);

    if (tokenNotice) {
      controls.notice.style.display = "block";
      setText(controls.notice, tokenNotice);
    }

    if (lifecycle === "running") {
      setText(controls.primary, busy ? "Stopping…" : "Stop Auto Trading");
      controls.primary.classList.remove("join");
      controls.primary.classList.add("stop");
      controls.primary.dataset.action = "stop";

      controls.pauseResume.style.display = "inline-flex";
      setText(controls.pauseResume, busy ? "Pausing…" : "Pause Auto Trading");
      controls.pauseResume.classList.remove("stop");
      controls.pauseResume.classList.add("join");
      controls.pauseResume.dataset.action = "pause";

      const informative = Boolean(reason && !["active", "connecting", "validating"].includes(status));
      controls.notice.style.display = tokenNotice || informative ? "block" : "none";
      if (!tokenNotice && informative) setText(controls.notice, reason);
    } else if (lifecycle === "paused") {
      setText(controls.primary, busy ? "Stopping…" : "Stop Auto Trading");
      controls.primary.classList.remove("join");
      controls.primary.classList.add("stop");
      controls.primary.dataset.action = "stop";

      controls.pauseResume.style.display = "inline-flex";
      setText(controls.pauseResume, busy ? "Resuming…" : "Resume Auto Trading");
      controls.pauseResume.classList.remove("stop");
      controls.pauseResume.classList.add("join");
      controls.pauseResume.dataset.action = "resume";

      controls.notice.style.display = "block";
      if (!tokenNotice) {
        setText(
          controls.notice,
          reason || `${modeLabel} trading is paused. Resume continues this same account mode; Stop clears recovery and starts fresh next time.`
        );
      }
    } else {
      setText(controls.primary, busy ? "Starting…" : "Start Auto Trade");
      controls.primary.classList.remove("stop");
      controls.primary.classList.add("join");
      controls.primary.dataset.action = "start";
      controls.pauseResume.style.display = "none";
      controls.notice.style.display = "block";
      if (!tokenNotice) {
        setText(
          controls.notice,
          reason || `${modeLabel} trading is not started. It will not execute until you press Start Auto Trade on this account mode.`
        );
      }
    }

    controls.primary.title = tokenNotice
      ? "Link your Deriv API token with trade scope in Settings before trading."
      : "";
    controls.pauseResume.title = tokenNotice
      ? "Link your Deriv API token with trade scope in Settings before trading."
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
      const action = primary.dataset.action || (lifecycleFrom(state) === "stopped" ? "start" : "stop");
      perform(action);
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

  window.setInterval(() => {
    if (state?.authenticated) render(state);
  }, 1000);
  window.setInterval(loadMe, 7000);
})();
