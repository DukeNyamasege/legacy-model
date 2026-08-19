(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_PIP_PRECISION_V1__) return;
  window.__DERIVADMIN_DIRECT_PIP_PRECISION_V1__ = true;

  const UpstreamWebSocket = window.WebSocket;
  const pipBySymbol = new Map();

  function PrecisionWebSocket(url, protocols) {
    const socket = protocols === undefined
      ? new UpstreamWebSocket(url)
      : new UpstreamWebSocket(url, protocols);
    const publicSocket = String(url || "").includes("/trading/v1/options/ws/public");
    if (!publicSocket) return socket;

    socket.addEventListener("message", (event) => {
      let message = null;
      try { message = JSON.parse(String(event.data || "")); } catch (_) { return; }

      if (message?.msg_type === "history") {
        const symbol = String(message?.echo_req?.ticks_history || "").toUpperCase();
        const pip = Number(message?.pip_size);
        if (symbol && Number.isInteger(pip) && pip >= 0 && pip <= 12) pipBySymbol.set(symbol, pip);
        return;
      }

      if (message?.msg_type !== "tick" || !message?.tick) return;
      const symbol = String(message.tick.symbol || message?.echo_req?.ticks || "").toUpperCase();
      const livePip = Number(message.tick.pip_size);
      if (symbol && Number.isInteger(livePip) && livePip >= 0 && livePip <= 12) {
        pipBySymbol.set(symbol, livePip);
        return;
      }
      const cachedPip = pipBySymbol.get(symbol);
      if (!Number.isInteger(cachedPip)) return;

      // The direct strategy must see a precision-complete tick exactly once. Stop
      // the original precision-less event before the engine handler, then dispatch
      // an equivalent tick carrying the cached symbol pip size.
      event.stopImmediatePropagation();
      try {
        socket.dispatchEvent(new MessageEvent("message", {
          data: JSON.stringify({
            ...message,
            tick: { ...message.tick, pip_size: cachedPip },
          }),
        }));
      } catch (_) {}
    });
    return socket;
  }

  PrecisionWebSocket.prototype = UpstreamWebSocket.prototype;
  try { Object.setPrototypeOf(PrecisionWebSocket, UpstreamWebSocket); } catch (_) {}
  for (const key of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) {
    try { PrecisionWebSocket[key] = UpstreamWebSocket[key]; } catch (_) {}
  }
  window.WebSocket = PrecisionWebSocket;

  function lastDigit(symbol, quote, pipSize = null) {
    const pip = Number.isInteger(Number(pipSize)) ? Number(pipSize) : pipBySymbol.get(String(symbol || "").toUpperCase());
    const numeric = Number(quote);
    if (Number.isFinite(numeric) && Number.isInteger(pip) && pip >= 0 && pip <= 12) {
      const fixed = numeric.toFixed(pip);
      for (let index = fixed.length - 1; index >= 0; index -= 1) {
        if (/\d/.test(fixed[index])) return Number(fixed[index]);
      }
    }
    const digits = String(quote ?? "").replace(/[^0-9]/g, "");
    return digits ? Number(digits[digits.length - 1]) : null;
  }

  window.DERIVADMIN_DIRECT_PIP_PRECISION_V1 = Object.freeze({
    version: "20260818-direct-pip-precision-v1",
    precision: (symbol) => pipBySymbol.get(String(symbol || "").toUpperCase()),
    pip_size: (symbol) => pipBySymbol.get(String(symbol || "").toUpperCase()),
    last_digit: lastDigit,
  });
})();
