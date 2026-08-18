(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_CONTINUITY_CHECKPOINT_V1__) return;
  window.__DERIVADMIN_DIRECT_CONTINUITY_CHECKPOINT_V1__ = true;

  const JOURNAL_PREFIX = "derivadmin-direct-journal-v1:";
  let inFlight = false;

  function journalRowsSince(timestamp) {
    const rows = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (!key || !key.startsWith(JOURNAL_PREFIX)) continue;
      let values = [];
      try { values = JSON.parse(localStorage.getItem(key) || "[]"); } catch (_) {}
      if (!Array.isArray(values)) continue;
      for (const row of values) {
        const at = Date.parse(String(row?.at || ""));
        if (Number.isFinite(at) && at >= timestamp - 1000) rows.push(row);
      }
    }
    return rows.sort((a, b) => Date.parse(String(a.at || "")) - Date.parse(String(b.at || "")));
  }

  function checkpointPayload() {
    const engine = window.DERIVADMIN_DIRECT_EXECUTION_V1?.state?.();
    const fence = window.DERIVADMIN_DIRECT_FINANCIAL_FENCE_V1?.state?.();
    if (!engine?.running || engine?.owner !== "browser" || !fence?.armed || !fence?.epoch) return null;

    const rows = journalRowsSince(Number(fence.armedAt || Date.now()));
    let debt = 0;
    let consecutiveLosses = 0;
    let virtualWins = 0;
    let virtualLosses = 0;
    let virtualObservations = 0;

    for (const row of rows) {
      if (row?.mode === "real" && row?.state === "SETTLED") {
        const profit = Number(row.profit || 0);
        if (!Number.isFinite(profit)) continue;
        if (profit < 0) {
          debt += Math.abs(profit);
          consecutiveLosses += 1;
        } else {
          debt = Math.max(0, debt - profit);
          if (debt <= 0.009) {
            debt = 0;
            consecutiveLosses = 0;
          }
        }
      } else if (row?.mode === "virtual") {
        virtualObservations += 1;
        if (String(row.outcome || "").toUpperCase() === "WIN") virtualWins += 1;
        else {
          virtualWins = 0;
          virtualLosses += 1;
        }
      }
    }

    return {
      epoch: String(fence.epoch),
      runtime: {
        session_profit: Number(engine.session_profit || 0),
        recovery_debt: Math.round(debt * 100000000) / 100000000,
        consecutive_losses: consecutiveLosses,
        virtual_mode: Boolean(engine.virtual_mode),
        virtual_wins: virtualWins,
        virtual_losses: virtualLosses,
        virtual_observations: virtualObservations,
        open_contracts: Number(engine.open_contracts || 0),
      },
    };
  }

  function checkpoint() {
    if (inFlight) return;
    const payload = checkpointPayload();
    if (!payload) return;
    inFlight = true;
    try {
      const request = new XMLHttpRequest();
      request.open("POST", "/api/me/direct-execution/checkpoint", true);
      request.withCredentials = true;
      request.timeout = 3500;
      request.setRequestHeader("Content-Type", "application/json");
      request.onloadend = () => { inFlight = false; };
      request.onerror = () => { inFlight = false; };
      request.ontimeout = () => { inFlight = false; };
      request.send(JSON.stringify(payload));
    } catch (_) {
      inFlight = false;
    }
  }

  const timer = setInterval(checkpoint, 5000);
  window.addEventListener("pagehide", () => {
    clearInterval(timer);
    checkpoint();
  }, { once: true });

  window.DERIVADMIN_DIRECT_CONTINUITY_CHECKPOINT_V1 = Object.freeze({
    version: "20260818-direct-continuity-v1",
    checkpoint,
  });
})();
