(() => {
  "use strict";

  if (window.__FOA_VPS_SEAMLESS_EXPERIENCE__) return;
  window.__FOA_VPS_SEAMLESS_EXPERIENCE__ = true;

  const VERSION = "20260816-vps-seamless-1";
  const SAVED_DRAFT_KEY = "foa-vps-last-saved-builder-v1";
  const BUILDER_DRAFT_KEY = "foa-builder-draft-v2";
  const NativeWebSocket = window.WebSocket;
  const nativeFetch = window.fetch.bind(window);
  const nativeSetInterval = window.setInterval.bind(window);

  let actionLock = null;
  let scrollLock = null;
  let lastSnapshot = null;
  let scheduled = false;
  let builderDirty = true;

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }

  function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (_) {}
  }

  function currentDraft() {
    return storageGet(BUILDER_DRAFT_KEY) || "";
  }

  function recalculateDirty() {
    const draft = currentDraft();
    const saved = storageGet(SAVED_DRAFT_KEY);
    // The first Start after this upgrade deliberately takes the proven Save+Start
    // path once. After a successful save, unchanged strategies use instant Start.
    builderDirty = !saved || saved !== draft;
  }

  recalculateDirty();

  function liveSnapshotFresh() {
    const cache = window.FOA_NETLIFY_LIVE_CACHE;
    return document.documentElement.dataset.liveTransport === "connected"
      && cache
      && Date.now() - Number(cache.savedAt || 0) < 30000;
  }

  // dashboard-v2's legacy 15-second full-shell refresh was useful across Netlify,
  // but on the full VPS the signed WebSocket owns hot state. Keep the old refresh
  // only as a one-minute disconnected/stale fallback so the DOM is not rebuilt
  // beneath the user while realtime is healthy.
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

  function emitSnapshot(payload) {
    if (!payload || payload.type !== "snapshot") return;
    lastSnapshot = payload;
    window.dispatchEvent(new CustomEvent("foa:vps-live-snapshot", { detail: payload }));
  }

  // Observe the exact same signed WebSocket already used by the dashboard. This
  // adds no second socket and no polling. It simply exposes snapshots to the
  // premium live monitor before the compatibility client paints its DOM patches.
  function VPSObservedWebSocket(url, protocols) {
    const socket = protocols === undefined
      ? new NativeWebSocket(url)
      : new NativeWebSocket(url, protocols);
    socket.addEventListener("message", (event) => {
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
      pushLocalEvent("scanner_ready", "Strategy saved; instant Start is ready.");
    }

    if (response.ok && method === "GET" && url.includes("/me/live-snapshot")) {
      response.clone().json().then(emitSnapshot).catch(() => {});
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
      intent === "stop"
        ? "Stop requested; no new BUY may pass the persisted manual-stop barrier."
        : "Start requested; initializing the private account execution session.",
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
      String(payload?.message || (enabled
        ? "Auto trading accepted; scanner is starting."
        : "Auto trading stopped; scanner is idle.")),
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
      // One local same-origin confirmation read makes the button/status sharp even
      // before the next worker revision arrives. It is not a recurring poll.
      window.setTimeout(async () => {
        try {
          const check = await nativeFetch("/me/live-snapshot", {
            credentials: "same-origin",
            cache: "no-store",
            headers: { Accept: "application/json" },
          });
          if (check.ok) emitSnapshot(await check.json());
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

    // If the builder changed, retain the proven one-click Save+Start transaction
    // owned by dashboard-v2. The first Start after this upgrade also takes that
    // conservative path once. Every unchanged Start/Resume/Stop after that uses
    // the direct same-origin VPS action and avoids a redundant save/full refresh.
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
    if (localEvents.length > 5) localEvents.length = 5;
    schedulePaint();
  }

  function eventPresentation(event) {
    const type = String(event?.event || "scanner_ready");
    const map = {
      scanner_ready: ["Scanning", "scan"],
      condition_not_met: ["Condition not met", "wait"],
      condition_met: ["Condition met", "met"],
      trade_preparing: ["Preparing trade", "prepare"],
      trade_open: ["Trade purchased", "open"],
      virtual_observation: ["Virtual Hook", "virtual"],
      execution_cancelled: ["Stopped", "stop"],
    };
    return map[type] || ["Live update", "scan"];
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
    const rows = Array.isArray(lastSnapshot?.runtime_events)
      ? lastSnapshot.runtime_events
      : [];
    return rows;
  }

  function combinedEvents() {
    const rows = [...localEvents, ...serverEvents()];
    rows.sort((a, b) => Number(b?.emitted_at || 0) - Number(a?.emitted_at || 0));
    const seen = new Set();
    return rows.filter((row) => {
      const key = `${row.event}|${row.symbol}|${row.tick_sequence}|${row.message}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 6);
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
      message: active
        ? "Execution session is live; waiting for the next configured condition."
        : "Auto trading is stopped. Press Start when you are ready.",
      emitted_at: Date.now() / 1000,
    };
  }

  function renderMonitor() {
    const monitor = ensureMonitor();
    if (!monitor) return;
    const events = combinedEvents();
    const current = events[0] || defaultMonitorEvent();
    const [label, tone] = eventPresentation(current);
    const transport = document.documentElement.dataset.liveTransport || "connecting";
    const meta = [];
    if (current.symbol) meta.push(current.symbol);
    if (current.digit !== null && current.digit !== undefined) meta.push(`digit ${current.digit}`);
    if (Number(current.tick_sequence || 0) > 0) meta.push(`tick #${Number(current.tick_sequence).toLocaleString()}`);

    const signature = JSON.stringify([
      transport,
      current.event,
      current.message,
      current.symbol,
      current.tick_sequence,
      current.digit,
      events.slice(0, 5).map((row) => [row.event, row.symbol, row.tick_sequence, row.message]),
    ]);
    if (monitor.dataset.signature === signature) return;
    monitor.dataset.signature = signature;
    monitor.innerHTML = `
      <div class="foa-vps-monitor-head">
        <div><span class="foa-vps-pulse"></span><strong>Live Strategy Monitor</strong><small>VPS realtime decision stream</small></div>
        <span class="foa-vps-transport ${safe(transport)}">${safe(transport === "connected" ? "LIVE" : transport.toUpperCase())}</span>
      </div>
      <div class="foa-vps-current ${safe(tone)}">
        <span class="foa-vps-current-icon"></span>
        <div><strong>${safe(label)}</strong><p>${safe(current.message || "Watching the next tick.")}</p>${meta.length ? `<small>${safe(meta.join(" · "))}</small>` : ""}</div>
      </div>
      <div class="foa-vps-feed">
        ${events.slice(0, 5).map((row) => {
          const [rowLabel, rowTone] = eventPresentation(row);
          const rowMeta = [row.symbol, Number(row.tick_sequence || 0) > 0 ? `#${row.tick_sequence}` : ""].filter(Boolean).join(" · ");
          return `<div class="foa-vps-feed-row ${safe(rowTone)}"><i></i><strong>${safe(rowLabel)}</strong><span>${safe(row.message || "")}</span>${rowMeta ? `<small>${safe(rowMeta)}</small>` : ""}</div>`;
        }).join("")}
      </div>`;
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
