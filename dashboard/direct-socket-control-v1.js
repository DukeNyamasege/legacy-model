(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_SOCKET_CONTROL_V1__) return;
  window.__DERIVADMIN_DIRECT_SOCKET_CONTROL_V1__ = true;

  const UpstreamWebSocket = window.WebSocket;
  const authenticated = new Set();

  function ControlledWebSocket(url, protocols) {
    const socket = protocols === undefined
      ? new UpstreamWebSocket(url)
      : new UpstreamWebSocket(url, protocols);
    if (/\/trading\/v1\/options\/ws\/(demo|real)(?:\?|$)/.test(String(url || ""))) {
      authenticated.add(socket);
      socket.addEventListener("close", () => authenticated.delete(socket), { once: true });
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
    version: "20260818-direct-socket-control-v1",
    close_authenticated: closeAuthenticated,
    authenticated_count: () => authenticated.size,
  });
})();
