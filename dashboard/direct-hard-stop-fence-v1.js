(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_HARD_STOP_FENCE_V1__) return;
  window.__DERIVADMIN_DIRECT_HARD_STOP_FENCE_V1__ = true;

  /*
   * This wraps the already-installed Deriv financial WebSocket fence BEFORE the
   * execution engine creates its sockets.  Stop is synchronous: after hard_stop()
   * returns, any later WebSocket payload containing BUY is rejected locally even
   * if a server Stop acknowledgement is delayed.
   *
   * A successful /direct-execution/arm response is the only automatic re-arm.
   * Reset/Clear has no relationship to this state.
   */

  const PriorWebSocket = window.WebSocket;
  const priorFetch = window.fetch.bind(window);
  const state = {
    stopped: false,
    stoppedAt: 0,
    blockedBuys: 0,
  };

  function pathFor(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url;
      return new URL(String(raw || ""), location.origin).pathname.replace(/^\/api(?=\/)/, "");
    } catch (_) {
      return "";
    }
  }

  function methodFor(input, init) {
    return String(init?.method || (typeof input !== "string" ? input?.method : "") || "GET").toUpperCase();
  }

  function hardStop() {
    state.stopped = true;
    state.stoppedAt = Date.now();
    window.dispatchEvent(new CustomEvent("derivadmin:hard-stop", {
      detail: { stopped_at: state.stoppedAt, buy_allowed: false },
    }));
    return true;
  }

  function armAfterServerAck() {
    state.stopped = false;
    state.stoppedAt = 0;
    window.dispatchEvent(new CustomEvent("derivadmin:hard-stop-cleared", {
      detail: { buy_allowed: true },
    }));
  }

  window.fetch = async function hardStopFetch(input, init) {
    const path = pathFor(input);
    const method = methodFor(input, init);

    if (method === "POST" && ["/me/direct-execution/stop", "/me/stop-trading", "/me/pause-trading"].includes(path)) {
      hardStop();
    }

    const response = await priorFetch(input, init);
    if (path === "/me/direct-execution/arm" && method === "POST" && response?.ok) {
      armAfterServerAck();
    }
    return response;
  };

  function HardStopWebSocket(url, protocols) {
    const socket = protocols === undefined
      ? new PriorWebSocket(url)
      : new PriorWebSocket(url, protocols);
    const originalSend = socket.send.bind(socket);

    socket.send = function hardStopSend(data) {
      let payload = null;
      try {
        payload = typeof data === "string" ? JSON.parse(data) : null;
      } catch (_) {}
      if (payload && Object.prototype.hasOwnProperty.call(payload, "buy") && state.stopped) {
        state.blockedBuys += 1;
        throw new Error("Trading is stopped; BUY blocked locally");
      }
      return originalSend(data);
    };
    return socket;
  }

  HardStopWebSocket.prototype = PriorWebSocket.prototype;
  try { Object.setPrototypeOf(HardStopWebSocket, PriorWebSocket); } catch (_) {}
  for (const key of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) {
    try { HardStopWebSocket[key] = PriorWebSocket[key]; } catch (_) {}
  }
  window.WebSocket = HardStopWebSocket;

  window.DERIVADMIN_DIRECT_HARD_STOP_FENCE_V1 = Object.freeze({
    version: "20260818-browser-hard-stop-v1",
    hard_stop: hardStop,
    state: () => ({ ...state, buy_allowed: !state.stopped }),
  });
})();
