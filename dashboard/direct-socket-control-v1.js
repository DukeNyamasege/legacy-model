(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_SOCKET_CONTROL_V1__) return;
  window.__DERIVADMIN_DIRECT_SOCKET_CONTROL_V1__ = true;

  const UpstreamWebSocket = window.WebSocket;
  const authenticated = new Set();
  const keepaliveTimers = new Map();
  const KEEPALIVE_MS = 30000;

  function stopKeepalive(socket) {
    const timer = keepaliveTimers.get(socket);
    if (timer) clearInterval(timer);
    keepaliveTimers.delete(socket);
  }

  function sendKeepalive(socket) {
    if (!socket || socket.readyState !== UpstreamWebSocket.OPEN) return false;
    try {
      // Deriv documents { ping: 1 } as the keepalive request and recommends a
      // 30–60 second cadence. Keep req_id absent so keepalive traffic cannot
      // collide with the execution engine's request correlation sequence.
      socket.send(JSON.stringify({ ping: 1 }));
      return true;
    } catch (_) {
      return false;
    }
  }

  function startKeepalive(socket) {
    // Exactly one interval may own a socket. Re-entry clears any previous timer
    // first, which prevents reconnect cycles from leaking intervals.
    stopKeepalive(socket);
    sendKeepalive(socket);
    const timer = setInterval(() => {
      if (!sendKeepalive(socket)) stopKeepalive(socket);
    }, KEEPALIVE_MS);
    keepaliveTimers.set(socket, timer);
  }

  function ControlledWebSocket(url, protocols) {
    const socket = protocols === undefined
      ? new UpstreamWebSocket(url)
      : new UpstreamWebSocket(url, protocols);

    const urlText = String(url || "");
    const optionsSocket = /\/trading\/v1\/options\/ws\/(public|demo|real)(?:\?|$)/.test(urlText);
    const authenticatedOptionsSocket = /\/trading\/v1\/options\/ws\/(demo|real)(?:\?|$)/.test(urlText);

    if (authenticatedOptionsSocket) authenticated.add(socket);
    if (optionsSocket) {
      socket.addEventListener("open", () => startKeepalive(socket), { once: true });
      socket.addEventListener("close", () => {
        stopKeepalive(socket);
        authenticated.delete(socket);
      }, { once: true });
    }
    return socket;
  }

  ControlledWebSocket.prototype = UpstreamWebSocket.prototype;
  try { Object.setPrototypeOf(ControlledWebSocket, UpstreamWebSocket); } catch (_) {}
  for (const key of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) {
    try { ControlledWebSocket[key] = UpstreamWebSocket[key]; } catch (_) {}
  }
  window.WebSocket = ControlledWebSocket;

  function closeAuthenticated() {
    for (const socket of Array.from(authenticated)) {
      stopKeepalive(socket);
      try { socket.close(1000, "Deriv account switched"); } catch (_) {}
    }
    authenticated.clear();
  }

  const prior = window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1 || {};
  window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1 = Object.freeze({
    ...prior,
    close_authenticated: closeAuthenticated,
  });

  window.DERIVADMIN_DIRECT_SOCKET_CONTROL_V1 = Object.freeze({
    version: "20260822-direct-socket-control-v3-all-options-ping",
    close_authenticated: closeAuthenticated,
    authenticated_count: () => authenticated.size,
    keepalive_count: () => keepaliveTimers.size,
    keepalive_ms: KEEPALIVE_MS,
  });
})();
