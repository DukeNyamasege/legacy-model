(() => {
  "use strict";

  if (window.__DERIVADMIN_DIRECT_CONTINUITY_CHECKPOINT_V1__) return;
  window.__DERIVADMIN_DIRECT_CONTINUITY_CHECKPOINT_V1__ = true;

  const JOURNAL_PREFIX = "derivadmin-direct-journal-v1:";
  const CHECKPOINT_TIMEOUT_MS = 10000;
  const RETRY_AFTER_FLIGHT_MS = 1000;
  let inFlight = false;
  let retryAfterFlight = false;

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
    const openContracts = new Map();

    for (const row of rows) {
      if (row?.mode === "real" && row?.state === "OPEN" && row?.contract_id) {
        openContracts.set(String(row.contract_id), row);
      }
      if (row?.mode === "real" && row?.state === "SETTLED") {
        if (row?.contract_id) openContracts.delete(String(row.contract_id));
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

    // The engine is the exact live financial ledger. Journal reconstruction is
    // retained only as a compatibility fallback for older cached builds.
    const engineDebt = Number(engine.recovery_debt);
    const exactDebt = Number.isFinite(engineDebt) ? Math.max(0, engineDebt) : debt;
    const engineLosses = Number(engine.consecutive_losses);
    const exactLosses = Number.isFinite(engineLosses) ? Math.max(0, Math.trunc(engineLosses)) : consecutiveLosses;
    const splitBasis = Number(engine.split_basis_debt || 0);
    const splitRemaining = Number(engine.split_remaining_wins || 0);
    const splitPartStake = Number(engine.split_part_stake || 0);

    return {
      epoch: String(fence.epoch),
      runtime: {
        session_profit: Number(engine.session_profit || 0),
        recovery_debt: Math.round(exactDebt * 100000000) / 100000000,
        split_basis_debt: Number.isFinite(splitBasis) ? Math.max(0, splitBasis) : 0,
        split_remaining_wins: Number.isFinite(splitRemaining) ? Math.max(0, Math.trunc(splitRemaining)) : 0,
        split_part_stake: Number.isFinite(splitPartStake) ? Math.max(0, splitPartStake) : 0,
        consecutive_losses: exactLosses,
        virtual_mode: Boolean(engine.virtual_mode),
        virtual_wins: virtualWins,
        virtual_losses: virtualLosses,
        virtual_observations: virtualObservations,
        open_contracts: openContracts.size,
        open_contract_ids: Array.from(openContracts.keys()).slice(0, 20),
      },
    };
  }

  function checkpoint() {
    if (inFlight) {
      retryAfterFlight = true;
      return;
    }
    const payload = checkpointPayload();
    if (!payload) return;
    inFlight = true;
    try {
      const request = new XMLHttpRequest();
      request.open("POST", "/api/me/direct-execution/checkpoint", true);
      request.withCredentials = true;
      // The VPS can legitimately take several seconds while persisting the exact
      // recovery handoff. Do not declare failure at 3.5s while the server is still
      // writing, because that creates overlapping checkpoint retry bursts.
      request.timeout = CHECKPOINT_TIMEOUT_MS;
      request.setRequestHeader("Content-Type", "application/json");
      const done = () => {
        inFlight = false;
        if (retryAfterFlight) {
          retryAfterFlight = false;
          setTimeout(checkpoint, RETRY_AFTER_FLIGHT_MS);
        }
      };
      request.onloadend = done;
      request.onerror = done;
      request.ontimeout = done;
      request.send(JSON.stringify(payload));
    } catch (_) {
      inFlight = false;
    }
  }

  const timer = setInterval(checkpoint, 5000);
  // OPEN and SETTLED journal events are safety-critical handoff boundaries. Push a
  // checkpoint immediately instead of waiting for the next five-second interval.
  window.addEventListener("derivadmin:direct-trade", () => setTimeout(checkpoint, 0));
  window.addEventListener("pagehide", () => {
    clearInterval(timer);
    checkpoint();
  }, { once: true });

  window.DERIVADMIN_DIRECT_CONTINUITY_CHECKPOINT_V1 = Object.freeze({
    version: "20260820-direct-continuity-v4-no-retry-burst",
    checkpoint,
  });
})();
