(() => {
  "use strict";

  if (window.__FOA_VPS_SEAMLESS_EXPERIENCE__) return;
  window.__FOA_VPS_SEAMLESS_EXPERIENCE__ = true;

  const VERSION = "20260816-vps-seamless-2";
  const SAVED_DRAFT_KEY = "foa-vps-last-saved-builder-v1";
  const BUILDER_DRAFT_KEY = "foa-builder-draft-v2";
  const SESSION_KEY = "foa-session-v2";
  const AUTH_RECOVERY_KEY = "foa-vps-auth-shell-recovery-v1";
  const NativeWebSocket = window.WebSocket;
  const nativeFetch = window.fetch.bind(window);
  const nativeSetInterval = window.setInterval.bind(window);

  let actionLock = null;
  let scrollLock = null;
  let lastSnapshot = null;
  let lastRealtimeAt = 0;
  let scheduled = false;
  let builderDirty = true;

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }

  function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (_) {}
  }

  function sessionGet(key) {
    try { return sessionStorage.getItem(key); } catch (_) { return null; }
  }

  function sessionSet(key, value) {
    try { sessionStorage.setItem(key, value); } catch (_) {}
  }

  function currentDraft() {
    return storageGet(BUILDER_DRAFT_KEY) || "";
  }

  function recalculateDirty() {
    const draft = currentDraft();
    const saved = storageGet(SAVED_DRAFT_KEY);
    builderDirty = !saved || saved !== draft;
  }

  recalculateDirty();

  function liveSnapshotFresh() {
    return document.documentElement.dataset.liveTransport === "connected"
      && lastRealtimeAt > 0
      && Date.now() - lastRealtimeAt < 35000;
  }

  // The old compatibility renderer rebuilt the whole dashboard every 15 seconds.
  // A healthy same-origin signed WebSocket now owns hot state; a full refresh is
  // only a disconnected fallback and never runs underneath normal interaction.
  window.setInterval = function vpsAwareSetInterval(callback, delay, ...args) {
    const source = String(callback || "");
    if (Number(delay) === 15000 && source.includes("refresh(false")) {
      return nativeSetInterval(() => {
        if (!liveSnapshotFresh()) callback();
      }, 60000, ...args);
    }
    return nativeSetInterval(callback, delay, ...args);
  };

  document.addEventListener("DOMContentLoaded", () => {
    window.setTimeout(() => {
      if (window.setInterval?.name === "vpsAwareSetInterval") {
        window.setInterval = nativeSetInterval;
      }
    }, 0);
  }, { once: true });

  function rememberAuthenticatedSession(payload) {
    const me = payload?.me || {};
    if (!(payload?.authenticated === true || me?.authenticated === true)) return false;
    const remembered = {
      authenticated: true,
      saved_at: Date.now(),
      account_type: me.account_type || "demo",
      available_account_types: Array.isArray(me.available_account_types)
        ? me.available_account_types
        : [me.account_type || "demo"],
      label: me.label || me.account_id_masked || "Deriv account",
      account_id_masked: me.account_id_masked || me.account_id || "",
      currency: me.currency || "USD",
      enabled: Boolean(me.enabled ?? payload?.lifecycle?.enabled),
      has_trading_api_token: Boolean(me.has_trading_api_token),
      requires_api_token: Boolean(me.requires_api_token),
      trading_api_token_invalid: Boolean(me.trading_api_token_invalid),
      settings: me.settings || {},
    };
    storageSet(SESSION_KEY, JSON.stringify(remembered));
    return true;
  }

  function recoverAuthenticatedShell(payload) {
    if (!rememberAuthenticatedSession(payload)) return;
    const landing = document.querySelector(".foa-landing-v2, .public-builder");
    if (!landing) return;

    // A transient /me timeout may have made dashboard-v2 choose its public shell
    // even though the signed socket already proved the HttpOnly session is valid.
    // Persist the socket-proven session and reload once so the next first paint is
    // authenticated. The sessionStorage guard prevents any reload loop.
    const previous = Number(sessionGet(AUTH_RECOVERY_KEY) || 0);
    if (Date.now() - previous < 15000) return;
    sessionSet(AUTH_RECOVERY_KEY, String(Date.now()));
    document.documentElement.dataset.authShellRecovery = "true";
    window.location.reload();
  }

  function emitSnapshot(payload) {
    if (!payload || payload.type !== "snapshot") return;
    lastSnapshot = payload;
    recoverAuthenticatedShell(payload);
    window.dispatchEvent(new CustomEvent("foa:vps-live-snapshot", { detail: payload }));
  }

  // Observe the same signed WebSocket already used by the dashboard. No second
  // socket and no polling are introduced. Heartbeats also count as realtime
  // freshness, so an idle but healthy dashboard does not trigger HTTP refreshes.
  function VPSObservedWebSocket(url, protocols) {
    const socket = protocols === undefined
      ? new NativeWebSocket(url)
      : new NativeWebSocket(url, protocols);
    socket.addEventListener("message", (event) => {
      lastRealtimeAt = Date.now();
      try { emitSnapshot(JSON.parse(event.data || "{}")); } catch (_) {}
    });
    return socket;
  }
  VPSObservedWebSocket.prototype = NativeWebSocket.prototype;
  Object.setPrototypeOf(VPSObservedWebSocket, NativeWebSocket);
  ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach((key) => {
    Object.defineProperty(VPSObservedWebSocket, key, {
      value: NativeWebSocket[key],
      enumerable: true,
    });
  });
  window.WebSocket = VPSObservedWebSocket;

  function urlOf(input) {
    if (typeof input === "string") return input;
    return String(input?.url || "");
  }

  function methodOf(input, init) {
    return String(init?.method || input?.method || "GET").toUpperCase();
  }

  window.fetch = async (input, init = {}) => {
    const response = await nativeFetch(input, init);
    const url = urlOf(input);
    const method = methodOf(input, init);

    if (response.ok && method === "POST" && url.includes("/me/custom-strategy")) {
      storageSet(SAVED_DRAFT_KEY, currentDraft());
      builderDirty = false;
      pushLocalEvent("scanner_ready", "Strategy saved. Scanner ready.");
    }

    if (response.ok && method === "GET" && url.includes("/me/live-snapshot")) {
      response.clone().json().then((payload) => {
        lastRealtimeAt = Date.now();
        emitSnapshot(payload);
      }).catch(() => {});
    }

    if (response.ok && method === "POST" && (
      url.includes("/me/resume-trading") || url.includes("/me/stop-trading")
    )) {
      response.clone().json().then((payload) => {
        const enabled = url.includes("/me/resume-trading");
        applyActionResult(enabled, payload || {});
      }).catch(() => {});
    }
    return response;
  };

  function actionButtons() {
    return Array.from(document.querySelectorAll("[data-main-action]"));
  }

  function runtimeActive(snapshot = lastSnapshot) {
    const life = snapshot?.lifecycle || window.FOA_NETLIFY_LIVE_CACHE?.lifecycle || {};
    if (typeof life.enabled === "boolean") return life.enabled;
    return Boolean(snapshot?.me?.enabled || window.FOA_NETLIFY_LIVE_CACHE?.me?.enabled);
  }

  function actionLabel(intent, confirmed = false) {
    if (intent === "stop") return confirmed ? "Start Auto Trading" : "Stopping Auto Trading...";
    if (intent === "resume") return confirmed ? "Stop Auto Trading" : "Resuming Auto Trading...";
    return confirmed ? "Stop Auto Trading" : "Starting Auto Trading...";
  }

  function applyActionLock() {
    if (!actionLock) return;
    const confirmed = Boolean(actionLock.confirmed);
    actionButtons().forEach((button) => {
      button.classList.add("foa-vps-action-button");
      button.setAttribute("aria-busy", confirmed ? "false" : "true");
      button.textContent = actionLabel(actionLock.intent, confirmed);
      if (confirmed) {
        const active = actionLock.intent !== "stop";
        button.dataset.mainAction = active ? "stop" : "start";
        button.classList.toggle("danger", active);
      }
      button.disabled = !confirmed;
    });
    document.body.classList.toggle("foa-vps-action-inflight", !confirmed);
  }

  function beginAction(intent) {
    actionLock = {
      intent,
      confirmed: false,
      startedAt: Date.now(),
      expiresAt: Date.now() + 9000,
    };
    scrollLock = {
      x: window.scrollX,
      y: window.scrollY,
      expiresAt: Date.now() + 3500,
    };
    pushLocalEvent(
      intent === "stop" ? "execution_cancelled" : "scanner_ready",
      intent === "stop" ? "Stopped. No new trade will be started." : "Starting scanner and execution stream...",
    );
    applyActionLock();
  }

  function applyActionResult(enabled, payload) {
    if (!actionLock) return;
    if (enabled && actionLock.intent === "stop") return;
    if (!enabled && actionLock.intent !== "stop") return;
    actionLock.confirmed = true;
    actionLock.expiresAt = Date.now() + 5000;
    pushLocalEvent(
      enabled ? "scanner_ready" : "execution_cancelled",
      String(payload?.message || (enabled ? "Scanning is starting." : "Auto trading stopped.")),
    );
    applyActionLock();
  }

  function clearActionLock() {
    actionLock = null;
    scrollLock = null;
    document.body.classList.remove("foa-vps-action-inflight");
  }

  async function fastMainAction(action) {
    try {
      const response = await nativeFetch(
        action === "stop" ? "/me/stop-trading" : "/me/resume-trading",
        {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify(action === "stop" ? {} : {
            mode: action === "resume" ? "continue" : "start_again",
          }),
        },
      );
      let payload = {};
      try { payload = await response.json(); } catch (_) {}
      if (!response.ok) {
        throw new Error(payload?.detail || payload?.message || `Trading action failed (${response.status})`);
      }
      applyActionResult(action !== "stop", payload);
      window.setTimeout(async () => {
        try {
          const check = await nativeFetch("/me/live-snapshot", {
            credentials: "same-origin",
            cache: "no-store",
            headers: { Accept: "application/json" },
          });
          if (check.ok) {
            lastRealtimeAt = Date.now();
            emitSnapshot(await check.json());
          }
        } catch (_) {}
      }, 120);
    } catch (error) {
      pushLocalEvent("execution_cancelled", String(error?.message || error));
      clearActionLock();
      window.alert(String(error?.message || error));
      schedulePaint();
    }
  }

  document.addEventListener("input", (event) => {
    if (event.target?.matches?.("[data-builder]")) builderDirty = true;
  }, true);
  document.addEventListener("change", (event) => {
    if (event.target?.matches?.("[data-builder],[data-market-select]")) builderDirty = true;
  }, true);

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.(
      "[data-strategy-mode],[data-market-mode],[data-market-remove],[data-trade-group],[data-reset-strategy]",
    )) {
      builderDirty = true;
    }

    const button = event.target?.closest?.("[data-main-action]");
    if (!button) return;
    const action = String(button.dataset.mainAction || "start").toLowerCase();

    if (actionLock && !actionLock.confirmed && Date.now() < actionLock.expiresAt) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }

    beginAction(action);
    if (action === "start" && builderDirty) return;

    event.preventDefault();
    event.stopImmediatePropagation();
    void fastMainAction(action);
  }, true);

  const localEvents = [];

  function pushLocalEvent(event, message) {
    localEvents.unshift({
      event,
      message,
      symbol: "",
      tick_sequence: 0,
      digit: null,
      emitted_at: Date.now() / 1000,
      local: true,
    });
    if (localEvents.length > 3) localEvents.length = 3;
    schedulePaint();
  }

  function eventPresentation(event) {
    const type = String(event?.event || "scanner_ready");
    const map = {
      scanner_ready: ["Scanning", "scan"],
      condition_not_met: ["Scanning", "wait"],
      condition_met: ["Matched", "met"],
      trade_preparing: ["Executing", "prepare"],
      trade_open: ["Purchased", "open"],
      virtual_observation: ["Virtual", "virtual"],
      execution_cancelled: ["Stopped", "stop"],
    };
    return map[type] || ["Scanning", "scan"];
  }

  function safe(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function serverEvents() {
    return Array.isArray(lastSnapshot?.runtime_events) ? lastSnapshot.runtime_events : [];
  }

  function currentEvent() {
    const rows = [...localEvents, ...serverEvents()];
    rows.sort((a, b) => Number(b?.emitted_at || 0) - Number(a?.emitted_at || 0));
    return rows[0] || null;
  }

  function monitorHost() {
    return document.querySelector(".strategy-builder-card .builder-status-line")
      || document.querySelector(".trades-control-panel");
  }

  function ensureMonitor() {
    const host = monitorHost();
    if (!host) return null;
    let monitor = document.querySelector("#foa-vps-live-monitor");
    if (monitor && monitor.parentNode) return monitor;
    monitor = document.createElement("section");
    monitor.id = "foa-vps-live-monitor";
    monitor.className = "foa-vps-live-monitor";
    monitor.setAttribute("aria-live", "polite");
    host.insertAdjacentElement("afterend", monitor);
    return monitor;
  }

  function defaultMonitorEvent() {
    const active = runtimeActive();
    return {
      event: active ? "scanner_ready" : "execution_cancelled",
      message: active ? "Scanning configured markets and conditions..." : "Auto trading stopped.",
      emitted_at: Date.now() / 1000,
    };
  }

  function renderMonitor() {
    const monitor = ensureMonitor();
    if (!monitor) return;
    const current = currentEvent() || defaultMonitorEvent();
    const [label, tone] = eventPresentation(current);
    const meta = [];
    if (current.symbol) meta.push(current.symbol);
    if (current.digit !== null && current.digit !== undefined) meta.push(`digit ${current.digit}`);

    const signature = JSON.stringify([
      current.event,
      current.message,
      current.symbol,
      current.digit,
    ]);
    if (monitor.dataset.signature === signature) return;
    monitor.dataset.signature = signature;
    monitor.innerHTML = `
      <span class="foa-vps-current-icon ${safe(tone)}"></span>
      <strong>${safe(label)}</strong>
      <span class="foa-vps-scan-copy">${safe(current.message || "Scanning...")}</span>
      ${meta.length ? `<small>${safe(meta.join(" · "))}</small>` : ""}`;
  }

  function handleSnapshot(snapshot) {
    lastSnapshot = snapshot;
    const enabled = Boolean(snapshot?.lifecycle?.enabled);
    if (actionLock) {
      const satisfied = actionLock.intent === "stop" ? !enabled : enabled;
      if (satisfied) {
        actionLock.confirmed = true;
        applyActionLock();
        window.setTimeout(() => {
          clearActionLock();
          schedulePaint();
        }, 180);
      }
    }
    renderMonitor();
  }

  window.addEventListener("foa:vps-live-snapshot", (event) => handleSnapshot(event.detail || {}));

  function restoreScrollIfNeeded() {
    if (!scrollLock || Date.now() >= scrollLock.expiresAt) return;
    if (Math.abs(window.scrollY - scrollLock.y) > 48 || Math.abs(window.scrollX - scrollLock.x) > 48) {
      window.scrollTo({ left: scrollLock.x, top: scrollLock.y, behavior: "instant" });
    }
  }

  function paint() {
    scheduled = false;
    if (actionLock && Date.now() >= actionLock.expiresAt) clearActionLock();
    applyActionLock();
    restoreScrollIfNeeded();
    renderMonitor();
  }

  function schedulePaint() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      paint();
      window.setTimeout(paint, 0);
    });
  }

  new MutationObserver(schedulePaint).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) schedulePaint();
  });
  window.addEventListener("pageshow", schedulePaint);
  window.addEventListener("focus", schedulePaint);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", schedulePaint, { once: true })
    : schedulePaint();

  window.FOA_VPS_SEAMLESS_EXPERIENCE_VERSION = VERSION;
})();
