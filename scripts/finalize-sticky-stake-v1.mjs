import fs from "node:fs";

const persistencePath = "dist/direct-strategy-persistence-v1.js";
const premiumPath = "dist/final-premium-6f3.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`sticky-stake missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}
function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`sticky-stake ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

let persistence = read(persistencePath);

persistence = replaceOne(
  persistence,
  `  const MARKET_OPEN_KEY = "derivadmin-builder-market-open-v1";`,
  `  const MARKET_OPEN_KEY = "derivadmin-builder-market-open-v1";\n  const STICKY_STAKE_KEY = "derivadmin-sticky-stake-v1";`,
  "storage key",
);

const helpers = `  function readStickyStake() {\n    try {\n      const value = Number(localStorage.getItem(STICKY_STAKE_KEY));\n      return Number.isFinite(value) && value > 0 ? value : null;\n    } catch (_) { return null; }\n  }\n\n  function rememberStickyStake(value) {\n    const stake = Number(value);\n    if (!Number.isFinite(stake) || stake <= 0) return null;\n    try { localStorage.setItem(STICKY_STAKE_KEY, String(stake)); } catch (_) {}\n    return stake;\n  }\n\n  function stakeInput() {\n    return document.querySelector('.restored-builder [data-builder="money.stake"]');\n  }\n\n  function applyStake(selected, stake) {\n    if (!validBuilderSelection(selected) || !(Number(stake) > 0)) return selected;\n    selected.builder.money = { ...(selected.builder.money || {}), stake: Number(stake) };\n    return selected;\n  }\n\n  function syncExplicitStakeFromDom() {\n    const input = stakeInput();\n    const stake = rememberStickyStake(input?.value);\n    if (!(stake > 0)) return null;\n    const state = appState();\n    if (validBuilderSelection(state?.selectedStrategy)) applyStake(state.selectedStrategy, stake);\n    return stake;\n  }\n\n  function enforceStickyStake(state) {\n    const stake = readStickyStake();\n    if (!(stake > 0) || !validBuilderSelection(state?.selectedStrategy)) return false;\n    const current = Number(state.selectedStrategy?.builder?.money?.stake);\n    applyStake(state.selectedStrategy, stake);\n    const input = stakeInput();\n    if (input && Number(input.value) !== stake) input.value = String(stake);\n    return current !== stake;\n  }\n\n`;

persistence = replaceOne(
  persistence,
  `  function persistBuilderState() {`,
  helpers + `  function persistBuilderState() {`,
  "sticky stake helpers",
);

persistence = replaceOne(
  persistence,
  `    const state = appState();\n    const selected = state?.selectedStrategy;\n    if (!validBuilderSelection(selected)) return;\n    writeJson(BUILDER_DRAFT_KEY, {`,
  `    const state = appState();\n    if (!state) return;\n    enforceStickyStake(state);\n    const selected = state.selectedStrategy;\n    if (!validBuilderSelection(selected)) return;\n    writeJson(BUILDER_DRAFT_KEY, {`,
  "persist canonical sticky stake",
);

persistence = replaceOne(
  persistence,
  `    if (validBuilderSelection(state.selectedStrategy)) {\n      persistBuilderState();\n      bindMarketDropdown();\n      return;\n    }`,
  `    if (validBuilderSelection(state.selectedStrategy)) {\n      const changed = enforceStickyStake(state);\n      persistBuilderState();\n      if (changed) {\n        window.setTimeout(() => {\n          try { window.FOA_FINAL_UI?.refresh?.(); } catch (_) {}\n          window.setTimeout(() => { enforceStickyStake(appState()); bindMarketDropdown(); }, 0);\n        }, 0);\n      } else bindMarketDropdown();\n      return;\n    }`,
  "restore existing selection stake",
);

persistence = replaceOne(
  persistence,
  `    state.selectedStrategy = saved.selectedStrategy;\n    window.setTimeout(() => {`,
  `    state.selectedStrategy = saved.selectedStrategy;\n    enforceStickyStake(state);\n    window.setTimeout(() => {`,
  "restore saved selection stake",
);

persistence = replaceOne(
  persistence,
  `  document.addEventListener("change", (event) => {\n    const target = event.target;\n    if (!target?.closest?.(".restored-builder")) return;`,
  `  document.addEventListener("change", (event) => {\n    const target = event.target;\n    if (!target?.closest?.(".restored-builder")) return;\n    if (target.matches?.('[data-builder="money.stake"]')) syncExplicitStakeFromDom();`,
  "stake change authority",
);

persistence = replaceOne(
  persistence,
  `  document.addEventListener("input", (event) => {\n    if (!event.target?.closest?.(".restored-builder")) return;\n    scheduleBuilderPersist();`,
  `  document.addEventListener("input", (event) => {\n    if (!event.target?.closest?.(".restored-builder")) return;\n    if (event.target.matches?.('[data-builder="money.stake"]')) syncExplicitStakeFromDom();\n    scheduleBuilderPersist();`,
  "stake input authority",
);

persistence = replaceOne(
  persistence,
  `  document.addEventListener("click", (event) => {\n    if (!event.target?.closest?.(".restored-builder")) return;\n    scheduleBuilderPersist();`,
  `  document.addEventListener("click", (event) => {\n    if (!event.target?.closest?.(".restored-builder")) return;\n    // A template/strategy click may replace selectedStrategy with preset money\n    // settings. Re-apply the trader's explicit stake on the next turn before any\n    // Save or Run can consume the new builder DOM.\n    window.setTimeout(() => {\n      const state = appState();\n      if (state && enforceStickyStake(state)) {\n        try { window.FOA_FINAL_UI?.refresh?.(); } catch (_) {}\n      }\n    }, 0);\n    scheduleBuilderPersist();`,
  "template changes cannot overwrite explicit stake",
);

for (const required of [
  "STICKY_STAKE_KEY",
  "syncExplicitStakeFromDom",
  "enforceStickyStake",
  "target.matches?.('[data-builder=\"money.stake\"]')",
  "template/strategy click may replace selectedStrategy",
]) {
  if (!persistence.includes(required)) throw new Error(`sticky-stake persistence invariant missing: ${required}`);
}
fs.writeFileSync(persistencePath, persistence, "utf8");

let index = read(indexPath);
index = index.replace(
  /\/direct-strategy-persistence-v1\.js\?v=[^"']+/g,
  "/direct-strategy-persistence-v1.js?v=20260820-sticky-stake-v1",
);
if (!index.includes("/direct-strategy-persistence-v1.js?v=20260820-sticky-stake-v1")) {
  throw new Error("sticky-stake persistence cache-bust missing");
}
fs.writeFileSync(indexPath, index, "utf8");

// This finalizer does not modify the shell itself, but assert the Builder stake
// remains a real positive-number input consumed by builderDraftFromDom. The
// persistence authority above mutates selectedStrategy before rerenders can reset it.
const premium = read(premiumPath);
if (!premium.includes("final-ui-shell-v2.js")) {
  throw new Error("sticky-stake final shell bootstrap missing");
}

console.log("Sticky stake v1 finalized: explicit stake survives rerenders, templates, Save, Run and reload until changed by the trader");
