(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_PIP_PRECISION_V1__) return;
  window.__DERIVADMIN_DIRECT_PIP_PRECISION_V1__ = true;

  const UpstreamWebSocket = window.WebSocket;
  const DEFAULT_PIP_BY_SYMBOL = Object.freeze({
    "1HZ10V": 4,
    "1HZ25V": 4,
    "1HZ50V": 4,
    "1HZ75V": 2,
    "1HZ100V": 2,
    "R_10": 2,
    "R_25": 2,
    "R_50": 2,
    "R_75": 2,
    "R_100": 2,
  });
  const pipBySymbol = new Map(Object.entries(DEFAULT_PIP_BY_SYMBOL));

  function normalizeSymbol(symbol) {
    return String(symbol || "").toUpperCase();
  }

  function validPip(value) {
    const pip = Number(value);
    return Number.isInteger(pip) && pip >= 0 && pip <= 12 ? pip : null;
  }

  function rememberPip(symbol, value) {
    const normalized = normalizeSymbol(symbol);
    const pip = validPip(value);
    if (normalized && pip !== null) pipBySymbol.set(normalized, pip);
  }

  function getPipSize(symbol) {
    const normalized = normalizeSymbol(symbol);
    return pipBySymbol.get(normalized) ?? DEFAULT_PIP_BY_SYMBOL[normalized] ?? null;
  }

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
        rememberPip(symbol, message?.pip_size);
        return;
      }

      if (message?.msg_type !== "tick" || !message?.tick) return;
      const symbol = String(message.tick.symbol || message?.echo_req?.ticks || "").toUpperCase();
      const livePip = validPip(message.tick.pip_size);
      if (symbol && livePip !== null) {
        pipBySymbol.set(symbol, livePip);
        return;
      }
      const cachedPip = getPipSize(symbol);
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

  function lastDigit(symbol, quote, explicitPipSize = null) {
    const explicitPip = validPip(explicitPipSize);
    const pip = explicitPip !== null ? explicitPip : getPipSize(symbol);
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
    defaults: DEFAULT_PIP_BY_SYMBOL,
    precision: getPipSize,
    pip_size: getPipSize,
    last_digit: lastDigit,
  });
})();
