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
      // Deriv's WebSocket ping endpoint requires only { ping: 1 }. Keeping req_id
      // absent prevents collisions with the execution engine's request sequence.
      socket.send(JSON.stringify({ ping: 1 }));
      return true;
    } catch (_) {
      return false;
    }
  }

  function startKeepalive(socket) {
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

    if (/\/trading\/v1\/options\/ws\/(demo|real)(?:\?|$)/.test(String(url || ""))) {
      authenticated.add(socket);
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
    version: "20260820-direct-socket-control-v2-private-ping",
    close_authenticated: closeAuthenticated,
    authenticated_count: () => authenticated.size,
    keepalive_count: () => keepaliveTimers.size,
    keepalive_ms: KEEPALIVE_MS,
  });
})();
