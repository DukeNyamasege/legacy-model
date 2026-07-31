(() => {
  "use strict";

  const originalFetch = window.fetch.bind(window);
  const perModeStoragePrefix = "legacy-dashboard-last-good-snapshot-v2:";

  function normalizedMode(value) {
    return String(value || "demo").toLowerCase() === "real" ? "real" : "demo";
  }

  function currentMode() {
    try {
      return normalizedMode(activeAccountType);
    } catch (_) {
      return "demo";
    }
  }

  function rewriteSummaryMode(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url;
      if (!raw) return input;
      const parsed = new URL(raw, window.location.href);
      if (parsed.pathname === "/metrics/summary") {
        parsed.searchParams.set("mode", currentMode());
        if (typeof input === "string") {
          return raw.startsWith("http")
            ? parsed.toString()
            : `${parsed.pathname}${parsed.search}${parsed.hash}`;
        }
        return new Request(parsed.toString(), input);
      }
    } catch (_) {}
    return input;
  }

  window.fetch = (input, init) => originalFetch(rewriteSummaryMode(input), init);

  const legacyRenderStatus = renderStatus;
  renderStatus = function modeSafeRenderStatus(data) {
    const payloadMode = normalizedMode(
      data?.dashboard_account_type || data?.mode || currentMode()
    );
    if (payloadMode !== currentMode()) return;
    legacyRenderStatus(data);
    try {
      localStorage.setItem(`${perModeStoragePrefix}${payloadMode}`, JSON.stringify(data));
    } catch (_) {}
  };

  function resetModeScopedState(mode) {
    latestSummary = null;
    lastGoodSnapshot = null;
    baseSystemPerformance = null;
    viewerSystemPerformance = null;
    lastModelDataVersion = null;
    try {
      const saved = JSON.parse(
        localStorage.getItem(`${perModeStoragePrefix}${mode}`) || "null"
      );
      if (
        saved?.system_performance
        && normalizedMode(saved.dashboard_account_type || saved.mode || mode) === mode
      ) {
        renderStatus(saved);
      }
    } catch (_) {}
  }

  function closeModeSocket() {
    window.clearTimeout(wsReconnectTimer);
    window.clearTimeout(wsWatchdogTimer);
    if (wsConnection) {
      try { wsConnection.close(); } catch (_) {}
    }
    wsConnection = null;
  }

  connectWebSocket = function connectModeAwareWebSocket() {
    if (document.hidden) return;
    if (wsConnection && wsConnection.readyState <= 1) return;
    const mode = currentMode();
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const root = apiBase
      ? apiBase.replace(/^https?:/, wsProtocol)
      : `${wsProtocol}//${window.location.host}`;
    const wsUrl = `${root}/ws/dashboard?mode=${encodeURIComponent(mode)}`;
    try {
      wsConnection = new WebSocket(wsUrl);
    } catch (_) {
      scheduleWsReconnect();
      return;
    }
    wsConnection.onopen = () => {
      wsReconnectAttempts = 0;
      lastWebSocketRefresh = Date.now();
      armWsWatchdog();
    };
    wsConnection.onmessage = event => {
      try {
        const message = JSON.parse(event.data);
        if (message.type !== "snapshot" || !message.data) return;
        const messageMode = normalizedMode(
          message.mode
          || message.data.dashboard_account_type
          || message.data.mode
        );
        if (messageMode !== currentMode()) return;
        lastWebSocketRefresh = Date.now();
        armWsWatchdog();
        renderStatus(message.data);
      } catch (_) {}
    };
    wsConnection.onclose = () => {
      window.clearTimeout(wsWatchdogTimer);
      wsConnection = null;
      scheduleWsReconnect();
    };
    wsConnection.onerror = () => {
      try { wsConnection.close(); } catch (_) {}
    };
  };

  const legacyRefresh = refresh;
  refresh = async function refreshCorrectAccountMode(options = {}) {
    const previousMode = currentMode();
    try {
      const response = await originalFetch(
        `${apiBase || ""}/me`,
        { credentials: "include", headers: { Accept: "application/json" } }
      );
      if (response.ok) {
        const me = await response.json();
        activeAccountType = me?.authenticated
          ? normalizedMode(me.account_type)
          : "demo";
        availableAccountTypes = Array.isArray(me?.available_account_types)
          ? me.available_account_types
          : [activeAccountType];
      }
    } catch (_) {}

    const nextMode = currentMode();
    if (nextMode !== previousMode) {
      closeModeSocket();
      resetModeScopedState(nextMode);
      updateAccountModeSwitch();
    }

    const result = await legacyRefresh(options);
    if (!wsConnection || wsConnection.readyState > 1) connectWebSocket();
    return result;
  };

  const legacySwitchAccountMode = switchAccountMode;
  switchAccountMode = async function switchModeAndRealtimeChannel(mode) {
    const target = normalizedMode(mode);
    if (target === currentMode()) return;
    closeModeSocket();
    resetModeScopedState(target);
    try {
      return await legacySwitchAccountMode(target);
    } finally {
      closeModeSocket();
      connectWebSocket();
    }
  };
})();
