import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const dist = path.join(root, "dist");

function read(name) {
  return fs.readFileSync(path.join(dist, name), "utf8");
}

function write(name, source) {
  fs.writeFileSync(path.join(dist, name), source, "utf8");
}

function replaceOnce(source, label, before, after) {
  const first = source.indexOf(before);
  if (first < 0) throw new Error(`Production v6 patch missing: ${label}`);
  if (source.indexOf(before, first + before.length) >= 0) {
    throw new Error(`Production v6 patch ambiguous: ${label}`);
  }
  return source.slice(0, first) + after + source.slice(first + before.length);
}

// ---------------------------------------------------------------------------
// 1. Public testing is permanently fail-open at the frontend premium bootstrap.
// ---------------------------------------------------------------------------
let premium = read("final-premium-6f3.js");
premium = premium.replace(
  'const VERSION = "20260818-testing-free-4";',
  'const VERSION = "20260818-testing-free-6";',
);
premium = replaceOnce(
  premium,
  "testing-free gate cannot render premium lock",
  `  function gate() {\n    state.locked = true;`,
  `  function gate() {\n    if (TESTING_FREE_ACCESS) {\n      state.locked = false;\n      document.documentElement.dataset.premiumState = "testing-free";\n      loadFinalApp({ realtime: true }).catch(() => {});\n      return;\n    }\n    state.locked = true;`,
);
premium = replaceOnce(
  premium,
  "testing-free boot catch fail-open",
  `      gate();\n    } catch (error) {\n      document.documentElement.dataset.premiumState = "error";`,
  `      gate();\n    } catch (error) {\n      if (TESTING_FREE_ACCESS) {\n        state.premium = { active: false, testing_free_access: true };\n        state.locked = false;\n        document.documentElement.dataset.premiumState = "testing-free";\n        document.documentElement.dataset.premiumBoot = "ready";\n        await loadFinalApp({ realtime: true });\n        return;\n      }\n      document.documentElement.dataset.premiumState = "error";`,
);
premium = replaceOnce(
  premium,
  "testing-free expiry cannot relock",
  `  async function exactExpiryReached() {\n    if (!state.premium?.active) return;`,
  `  async function exactExpiryReached() {\n    if (TESTING_FREE_ACCESS) return;\n    if (!state.premium?.active) return;`,
);
premium = premium.replaceAll(
  "/final-ui-shell-v2.js?v=20260818-local-ui-12",
  "/final-ui-shell-v2.js?v=20260818-production-v6",
);
write("final-premium-6f3.js", premium);

// ---------------------------------------------------------------------------
// 2. Browser-direct Split recovery: configured N successful equal profit legs.
// ---------------------------------------------------------------------------
let engine = read("deriv-direct-execution-v2.js");
engine = replaceOnce(
  engine,
  "browser split state",
  `    recoveryDebt: 0,\n    currentStake: 0.5,`,
  `    recoveryDebt: 0,\n    splitBasisDebt: 0,\n    splitRemainingWins: 0,\n    currentStake: 0.5,`,
);
engine = engine.replaceAll(
  `    state.recoveryDebt = 0;\n    state.currentStake = baseStake();`,
  `    state.recoveryDebt = 0;\n    state.splitBasisDebt = 0;\n    state.splitRemainingWins = 0;\n    state.currentStake = baseStake();`,
);

const recoveryPattern = /  function recoveryStake\(firstProposal\) \{[\s\S]*?\n  \}\n\n  async function executeReal/;
if (!recoveryPattern.test(engine)) throw new Error("Production v6 patch missing: recoveryStake");
engine = engine.replace(
  recoveryPattern,
  `  function recoveryStake(firstProposal) {\n    const base = baseStake();\n    const settings = state.strategy?.martingale || { mode: "system", multiplier: 2, split_count: 2 };\n    if (state.recoveryDebt <= 0.009) return base;\n    if (settings.mode === "multiplier") {\n      return Math.ceil(base * (Number(settings.multiplier || 2) ** Math.max(1, state.consecutiveLosses)) * 100) / 100;\n    }\n    const ratio = proposedProfitRatio(firstProposal, base) || state.lastProfitRatio;\n    if (ratio <= 0) return base;\n    if (settings.mode === "split") {\n      const parts = Math.max(1, Math.min(3, Number(settings.split_count || 1)));\n      if (state.splitBasisDebt <= 0.009 || state.splitRemainingWins <= 0) {\n        state.splitBasisDebt = state.recoveryDebt;\n        state.splitRemainingWins = parts;\n      }\n      const targetProfitPerSuccessfulLeg = state.splitBasisDebt / parts;\n      return Math.ceil(Math.max(base, targetProfitPerSuccessfulLeg / ratio) * 100) / 100;\n    }\n    const buffer = Math.max(0.05, state.recoveryDebt * 0.06);\n    return Math.ceil(Math.max(base, (state.recoveryDebt + buffer) / ratio) * 100) / 100;\n  }\n\n  async function executeReal`,
);

engine = replaceOnce(
  engine,
  "browser open contract remembers recovery leg",
  `        purchasedAt: Date.now(),\n        epoch,`,
  `        purchasedAt: Date.now(),\n        epoch,\n        recovery: state.recoveryDebt > 0.009,`,
);

engine = replaceOnce(
  engine,
  "browser split settlement ledger",
  `    if (profit < 0) {\n      state.consecutiveLosses += 1;\n      state.recoveryDebt = Math.max(0, state.recoveryDebt + Math.abs(profit));\n    } else {\n      state.recoveryDebt = Math.max(0, state.recoveryDebt - profit);\n      if (state.recoveryDebt <= 0.009) {\n        state.recoveryDebt = 0;\n        state.consecutiveLosses = 0;\n        state.currentStake = baseStake();\n      }\n    }`,
  `    const martingale = state.strategy?.martingale || {};\n    const splitMode = String(martingale.mode || "system") === "split";\n    const splitCount = Math.max(1, Math.min(3, Number(martingale.split_count || 1)));\n    const wasRecovery = Boolean(open.recovery);\n    if (profit < 0) {\n      state.consecutiveLosses += 1;\n      state.recoveryDebt = Math.max(0, state.recoveryDebt + Math.abs(profit));\n      if (splitMode) {\n        // A losing recovery does not consume a successful part. The enlarged real\n        // debt becomes a new equal Split-N loss pool.\n        state.splitBasisDebt = state.recoveryDebt;\n        state.splitRemainingWins = splitCount;\n      }\n    } else {\n      state.recoveryDebt = Math.max(0, state.recoveryDebt - profit);\n      if (splitMode && wasRecovery && state.recoveryDebt > 0.009) {\n        state.splitRemainingWins = Math.max(0, Number(state.splitRemainingWins || splitCount) - 1);\n        // Provider cent rounding may leave a tiny transparent residual. Never fall\n        // back to base while real debt exists; retain one cleanup success if needed.\n        if (state.splitRemainingWins <= 0) state.splitRemainingWins = 1;\n      }\n      if (state.recoveryDebt <= 0.009) {\n        state.recoveryDebt = 0;\n        state.splitBasisDebt = 0;\n        state.splitRemainingWins = 0;\n        state.consecutiveLosses = 0;\n        state.currentStake = baseStake();\n      }\n    }`,
);
write("deriv-direct-execution-v2.js", engine);

// ---------------------------------------------------------------------------
// 3. Run UX: Transactions contains transactions only; remove 400ms repaint loop.
// ---------------------------------------------------------------------------
let ux = read("direct-runtime-ux-v4.js");
ux = replaceOnce(
  ux,
  "remove strategy checker from Transactions",
  `    } else if (tab === "transactions") {\n      body.insertAdjacentHTML("afterbegin", strategyCard(true));\n    }`,
  `    }`,
);
ux = ux.replace(
  `      renderLoadedBadge();\n      renderStrategyCard();`,
  `      renderStrategyCard();`,
);
ux = ux.replace(
  `  setInterval(() => { unobserve(); try { renderRunState(); } finally { observe(); } }, 400);\n`,
  ``,
);
write("direct-runtime-ux-v4.js", ux);

// ---------------------------------------------------------------------------
// 4. Canonical transaction table: exact time + market + requested trade fields.
// ---------------------------------------------------------------------------
let shell = read("final-ui-shell-v2.js");
const tablePattern = /  function transactionTable\(rows, currency\) \{[\s\S]*?\n  \}\n\n  function transactionRow\(trade, currency\) \{[\s\S]*?\n  \}\n(?=\n  function )/;
if (!tablePattern.test(shell)) throw new Error("Production v6 patch missing: transaction table functions");
shell = shell.replace(
  tablePattern,
  `  function transactionMarketLabel(trade) {\n    const raw = String(trade.symbol || trade.market || trade.underlying_symbol || trade.underlying || "").toUpperCase();\n    const labels = {\n      "1HZ10V": "V10 (1s)", "1HZ25V": "V25 (1s)", "1HZ50V": "V50 (1s)",\n      "1HZ75V": "V75 (1s)", "1HZ100V": "V100 (1s)",\n      "R_10": "V10", "R_25": "V25", "R_50": "V50", "R_75": "V75", "R_100": "V100",\n    };\n    return labels[raw] ? \`\${labels[raw]} · \${raw}\` : (raw || "Deriv Options");\n  }\n\n  function transactionTimeLabel(trade) {\n    const raw = trade.purchase_time ?? trade.buy_time ?? trade.created_at ?? trade.timestamp ?? trade.date_start ?? trade.open_time ?? trade.at;\n    if (raw === null || raw === undefined || raw === "") return "--:--:--";\n    let date;\n    if (typeof raw === "number" || /^\\d+(?:\\.\\d+)?$/.test(String(raw))) {\n      const numeric = Number(raw);\n      date = new Date(numeric < 1e12 ? numeric * 1000 : numeric);\n    } else {\n      date = new Date(String(raw));\n    }\n    if (!Number.isFinite(date.getTime())) return "--:--:--";\n    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });\n  }\n\n  function transactionTable(rows, currency) {\n    return \`<div class="transaction-table transaction-table-v6">\n      <div class="transaction-head transaction-head-v6"><span>Time / Market</span><span>Type</span><span>Entry / Exit</span><span>Buy price</span><span>Profit / Loss</span></div>\n      <div class="transaction-rows">\${rows.slice(0, 100).map((trade) => transactionRow(trade, currency)).join("")}</div>\n    </div>\`;\n  }\n\n  function transactionRow(trade, currency) {\n    const profit = Number(trade.profit || 0);\n    const stake = Number(trade.stake ?? trade.buy_price ?? trade.price ?? 0);\n    const entry = trade.entry_tick ?? trade.entry_spot ?? trade.entrySpot ?? trade.buy_spot ?? "—";\n    const exit = trade.exit_tick ?? trade.exit_spot ?? trade.exitSpot ?? trade.sell_spot ?? "—";\n    const time = transactionTimeLabel(trade);\n    const market = transactionMarketLabel(trade);\n    return \`<div class="transaction-row transaction-row-v6">\n      <span class="tx-time-market"><small>\${esc(time)}</small><b>\${esc(market)}</b></span>\n      <span class="tx-type"><b>\${esc(contractLabel(trade))}</b></span>\n      <span class="tx-spots"><b>\${esc(entry)}</b><small>\${esc(exit)}</small></span>\n      <span class="tx-buy"><b>\${esc(money(stake, currency))}</b></span>\n      <strong class="\${profit >= 0 ? "positive" : "negative"}">\${profit >= 0 ? "+" : ""}\${esc(money(profit, currency))}</strong>\n    </div>\`;\n  }\n`,
);
write("final-ui-shell-v2.js", shell);

// ---------------------------------------------------------------------------
// 5. Cache bust the exact production assets so iOS cannot retain the old gate/UI.
// ---------------------------------------------------------------------------
let index = read("index.html");
index = index.replace(
  /\/final-premium-6f3\.js\?v=[^"']+/g,
  "/final-premium-6f3.js?v=20260818-production-v6",
);
index = index.replace(
  /\/public-testing-runtime-v1\.js\?v=[^"']+(?:&amp;hotfix=\d+)?/g,
  "/public-testing-runtime-v1.js?v=20260818-access-only-v7",
);
write("index.html", index);

console.log("Production v6 finalized: premium fail-open, hard Stop support, equal Split debt continuity, compact stable Run UI and transaction metadata");
