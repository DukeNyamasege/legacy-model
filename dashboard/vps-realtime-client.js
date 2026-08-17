(() => {
  "use strict";

  if (window.__DERIVADMIN_VPS_REALTIME_V1__) return;
  window.__DERIVADMIN_VPS_REALTIME_V1__ = true;

  const explicitStream = String(
    document.querySelector('meta[name="stream-base-url"]')?.content || "",
  ).trim().replace(/\/+$/, "");
  const streamBase = explicitStream || `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
  const FALLBACK_MS = 5000;

  let socket = null;
  let reconnectTimer = null;
  let fallbackTimer = null;
  let reconnectAttempt = 0;
  let connecting = false;

  function publish(raw) {
    if (!raw?.me?.authenticated) return;
    const snapshot = {
      savedAt: Date.now(),
      me: raw.me,
      lifecycle: raw.lifecycle || null,
      trades: raw.trades || null,
    };
    window.DERIVADMIN_LIVE_CACHE = snapshot;
    document.documentElement.dataset.liveTransport = "connected";
    document.dispatchEvent(new CustomEvent("derivadmin:live-snapshot", { detail: snapshot }));
  }

  async function fallbackSnapshot() {
    try {
      const response = await fetch("/me/live-snapshot", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      publish(await response.json());
    } catch (_) {}
  }

  function scheduleFallback() {
    window.clearTimeout(fallbackTimer);
    fallbackTimer = window.setTimeout(async () => {
      if (!socket || socket.readyState !== WebSocket.OPEN) await fallbackSnapshot();
      scheduleFallback();
    }, FALLBACK_MS);
  }

  function scheduleReconnect() {
    window.clearTimeout(reconnectTimer);
    const delay = Math.min(8000, 450 * (2 ** Math.min(reconnectAttempt, 5))) + Math.random() * 250;
    reconnectAttempt += 1;
    reconnectTimer = window.setTimeout(connect, delay);
  }

  async function connect() {
    if (connecting || document.hidden) return;
    if (socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(socket.readyState)) return;
    connecting = true;
    try {
      const ticketResponse = await fetch("/me/live-ticket", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!ticketResponse.ok) {
        if (ticketResponse.status === 401) return;
        throw new Error(`Realtime ticket returned ${ticketResponse.status}`);
      }
      const payload = await ticketResponse.json();
      const ticket = String(payload.ticket || "").trim();
      if (!ticket) throw new Error("Realtime ticket unavailable");

      socket = new WebSocket(`${streamBase}/ws/me/live?ticket=${encodeURIComponent(ticket)}`);
      socket.onopen = () => {
        reconnectAttempt = 0;
        document.documentElement.dataset.liveTransport = "connected";
      };
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (payload.type === "snapshot" || payload.me) publish(payload);
        } catch (_) {}
      };
      socket.onerror = () => {
        document.documentElement.dataset.liveTransport = "reconnecting";
      };
      socket.onclose = () => {
        socket = null;
        document.documentElement.dataset.liveTransport = "reconnecting";
        fallbackSnapshot();
        scheduleReconnect();
      };
    } catch (_) {
      document.documentElement.dataset.liveTransport = "fallback";
      fallbackSnapshot();
      scheduleReconnect();
    } finally {
      connecting = false;
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) connect();
  });
  window.addEventListener("pageshow", connect);
  window.addEventListener("online", connect);
  window.addEventListener("offline", () => {
    document.documentElement.dataset.liveTransport = "offline";
  });

  scheduleFallback();
  fallbackSnapshot();
  connect();

  window.DERIVADMIN_VPS_REALTIME_VERSION = "20260817-6f1-1";
})();