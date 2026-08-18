(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1__) return;
  window.__DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1__ = true;

  const NativeWebSocket = window.WebSocket;
  const nativeFetch = window.fetch.bind(window);
  const state = {
    armed: false,
    epoch: "",
    armedAt: 0,
    lastAckAt: 0,
    leaseMs: 20000,
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

  async function bodyJson(input, init) {
    if (typeof init?.body === "string") {
      try { return JSON.parse(init.body); } catch (_) { return {}; }
    }
    if (init?.body && typeof init.body === "object" && !(init.body instanceof FormData) && !(init.body instanceof Blob)) {
      return init.body;
    }
    if (typeof input !== "string" && input?.clone) {
      try { return await input.clone().json(); } catch (_) {}
    }
    return {};
  }

  function clearLease(epoch = "") {
    if (epoch && state.epoch && epoch !== state.epoch) return;
    state.armed = false;
    state.epoch = "";
    state.armedAt = 0;
    state.lastAckAt = 0;
  }

  window.fetch = async function directFinancialFenceFetch(input, init) {
    const path = pathFor(input);
    const method = methodFor(input, init);
    const isControl = method === "POST" && [
      "/me/direct-execution/arm",
      "/me/direct-execution/heartbeat",
      "/me/direct-execution/stop",
    ].includes(path);
    const body = isControl ? await bodyJson(input, init) : {};
    const response = await nativeFetch(input, init);

    if (path === "/me/direct-execution/arm" && method === "POST") {
      if (response.ok) {
        let payload = {};
        try { payload = await response.clone().json(); } catch (_) {}
        state.armed = true;
        state.epoch = String(body?.epoch || payload?.epoch || "");
        state.armedAt = Date.now();
        state.lastAckAt = state.armedAt;
        state.leaseMs = Math.max(10000, Number(payload?.lease_seconds || 20) * 1000);
        try {
          const channel = new BroadcastChannel("derivadmin-direct-execution-owner-v1");
          channel.postMessage({ type: "owner", epoch: state.epoch, at: state.armedAt });
          channel.close();
        } catch (_) {}
      } else {
        clearLease(String(body?.epoch || ""));
      }
    } else if (path === "/me/direct-execution/heartbeat" && method === "POST") {
      if (response.ok && String(body?.epoch || "") === state.epoch) {
        state.lastAckAt = Date.now();
      } else if (response.status === 409) {
        clearLease(String(body?.epoch || ""));
      }
    } else if (path === "/me/direct-execution/stop" && method === "POST") {
      clearLease(String(body?.epoch || ""));
    }
    return response;
  };

  try {
    const channel = new BroadcastChannel("derivadmin-direct-execution-owner-v1");
    channel.onmessage = (event) => {
      const incoming = String(event?.data?.epoch || "");
      if (event?.data?.type === "owner" && incoming && state.epoch && incoming !== state.epoch) {
        clearLease();
      }
    };
    window.addEventListener("pagehide", () => channel.close(), { once: true });
  } catch (_) {}

  function leaseAllowsBuy() {
    if (!state.armed || !state.epoch || !state.lastAckAt) return false;
    // Stop browser financial sends before the server lease can expire. This leaves
    // an 8-second no-owner buffer for a clean VPS takeover rather than overlap.
    return Date.now() - state.lastAckAt < Math.max(2500, state.leaseMs - 8000);
  }

  function GuardedWebSocket(url, protocols) {
    const socket = protocols === undefined
      ? new NativeWebSocket(url)
      : new NativeWebSocket(url, protocols);
    const nativeSend = socket.send.bind(socket);
    socket.send = function guardedSend(data) {
      let payload = null;
      try { payload = typeof data === "string" ? JSON.parse(data) : null; } catch (_) {}
      if (payload && Object.prototype.hasOwnProperty.call(payload, "buy") && !leaseAllowsBuy()) {
        throw new Error("Direct financial ownership is not active");
      }
      return nativeSend(data);
    };
    return socket;
  }

  GuardedWebSocket.prototype = NativeWebSocket.prototype;
  try { Object.setPrototypeOf(GuardedWebSocket, NativeWebSocket); } catch (_) {}
  for (const key of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) {
    try { GuardedWebSocket[key] = NativeWebSocket[key]; } catch (_) {}
  }
  window.WebSocket = GuardedWebSocket;

  window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1 = Object.freeze({
    version: "20260818-direct-financial-fence-v1",
    state: () => ({ ...state, buy_allowed: leaseAllowsBuy() }),
  });
})();
