(() => {
  "use strict";

  const CONTRACT_VERSION = "2";
  const FLAG_KEY = "legacy-dashboard-data-contract-version";
  const OLD_SNAPSHOT_KEY = "legacy-dashboard-last-good-snapshot-v1";

  // Do not resurrect a last-good snapshot created under the old mixed-ledger
  // contract after this release. New verified snapshots may use the same browser
  // storage key after the first live refresh.
  try {
    if (localStorage.getItem(FLAG_KEY) !== CONTRACT_VERSION) {
      localStorage.removeItem(OLD_SNAPSHOT_KEY);
      localStorage.setItem(FLAG_KEY, CONTRACT_VERSION);
    }
  } catch (_) {}

  const originalRenderStatus = window.renderStatus;
  let correctionInFlight = false;

  async function fetchCorrectMode(mode) {
    if (correctionInFlight || typeof originalRenderStatus !== "function") return;
    correctionInFlight = true;
    try {
      const response = await fetch(`/metrics/summary?mode=${encodeURIComponent(mode)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const data = await response.json();
      const contract = data?.data_consistency || {};
      const today = data?.system_performance?.today || {};
      const invariantOk = Number(today.total_trades || 0)
        === Number(today.wins || 0) + Number(today.losses || 0);
      if (contract.version === 2 && contract.invariant_ok && invariantOk) {
        originalRenderStatus(data);
      }
    } catch (_) {
      // The existing last-good snapshot/error handling remains authoritative.
    } finally {
      correctionInFlight = false;
    }
  }

  if (typeof originalRenderStatus === "function") {
    window.renderStatus = function renderConsistentStatus(data) {
      const selectedMode = String(document.body.dataset.accountMode || "demo").toLowerCase();
      const snapshotMode = String(data?.dashboard_account_type || "demo").toLowerCase();
      const contract = data?.data_consistency || {};
      const today = data?.system_performance?.today || {};
      const invariantOk = Number(today.total_trades || 0)
        === Number(today.wins || 0) + Number(today.losses || 0);

      if (snapshotMode !== selectedMode) {
        fetchCorrectMode(selectedMode);
        return;
      }
      // Once v2 data starts arriving, never paint a mathematically inconsistent
      // snapshot. A following server refresh will replace it.
      if (contract.version === 2 && (!contract.invariant_ok || !invariantOk)) {
        console.warn("Rejected inconsistent dashboard snapshot", {
          total: today.total_trades,
          wins: today.wins,
          losses: today.losses,
        });
        return;
      }
      return originalRenderStatus(data);
    };
  }

  // The main 'Without Martingale' card is the fixed $0.50 baseline. The stake
  // simulator has its own explicitly simulated fixed P/L field so changing the
  // slider cannot mutate or reinterpret the baseline cards.
  window.renderStakeSimulation = function renderConsistentStakeSimulation(today, selectedStake) {
    const martingale = Number(today?.simulated_martingale_pnl || 0);
    const fixed = Number(today?.simulated_fixed_pnl ?? today?.fixed_pnl ?? 0);
    const signed = value => `${value >= 0 ? "+" : "-"}$${Math.abs(value).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;

    const martingaleEl = document.getElementById("sim-pl-martingale");
    const fixedEl = document.getElementById("sim-pl-fixed");
    if (martingaleEl) {
      martingaleEl.textContent = signed(martingale);
      martingaleEl.style.color = martingale < 0 ? "var(--gbs-red)" : "var(--gbs-green)";
    }
    if (fixedEl) {
      fixedEl.textContent = signed(fixed);
      fixedEl.style.color = "var(--gbs-blue)";
    }
    const input = document.getElementById("stake-simulator-input");
    if (input && document.activeElement !== input) {
      input.value = Number(selectedStake || 0.50).toFixed(2);
    }
  };

  // Switching Demo/Real changes body[data-account-mode]. Fetch the matching
  // immutable snapshot immediately rather than waiting for a stale Demo WS frame
  // or the 30-second fallback timer.
  const observer = new MutationObserver(mutations => {
    if (!mutations.some(item => item.attributeName === "data-account-mode")) return;
    const mode = String(document.body.dataset.accountMode || "demo").toLowerCase();
    fetchCorrectMode(mode);
  });
  observer.observe(document.body, { attributes: true, attributeFilter: ["data-account-mode"] });
})();
