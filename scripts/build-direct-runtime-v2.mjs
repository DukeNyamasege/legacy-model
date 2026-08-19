import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const engineSourcePath = path.join(root, "dashboard", "deriv-direct-execution-v1.js");
const uxSourcePath = path.join(root, "dashboard", "direct-runtime-ux-v3.js");
const distDir = path.join(root, "dist");
const engineOut = path.join(distDir, "deriv-direct-execution-v2.js");
const uxOut = path.join(distDir, "direct-runtime-ux-v4.js");

function replaceOnce(source, label, before, after) {
  const first = source.indexOf(before);
  if (first < 0) throw new Error(`Direct runtime build patch missing: ${label}`);
  if (source.indexOf(before, first + before.length) >= 0) {
    throw new Error(`Direct runtime build patch is ambiguous: ${label}`);
  }
  return source.slice(0, first) + after + source.slice(first + before.length);
}

function ensureOnce(source, label, marker, before, after) {
  if (source.includes(marker)) return source;
  return replaceOnce(source, label, before, after);
}

function ensureCount(source, label, before, after, expected) {
  const oldCount = source.split(before).length - 1;
  const newCount = source.split(after).length - 1;
  if (oldCount === 0 && newCount === expected) return source;
  if (oldCount !== expected) {
    throw new Error(
      `Direct runtime build patch ${label} expected ${expected} old occurrence(s) or ${expected} installed occurrence(s), found old=${oldCount} installed=${newCount}`,
    );
  }
  return source.split(before).join(after);
}

fs.mkdirSync(distDir, { recursive: true });
let engine = fs.readFileSync(engineSourcePath, "utf8").replace(/\r\n/g, "\n");

// Comparator parity was originally a build-time migration. The canonical browser
// source now already carries all_even/all_odd, so patch older source only.
const hasAllEven = engine.includes('condition.operator === "all_even"');
const hasAllOdd = engine.includes('condition.operator === "all_odd"');
if (hasAllEven !== hasAllOdd) {
  throw new Error("Direct runtime comparator parity is only partially installed");
}
if (!hasAllEven) {
  engine = replaceOnce(
    engine,
    "browser all-even/all-odd comparator parity",
    `      if (condition.operator === "all_same") return sample.length > 0 && sample.every((digit) => digit === sample[0]);\n      return sample.every((digit) => compare(digit, condition.operator, Number(condition.value)));`,
    `      if (condition.operator === "all_same") return sample.length > 0 && sample.every((digit) => digit === sample[0]);\n      if (condition.operator === "all_even") return sample.length > 0 && sample.every((digit) => digit % 2 === 0);\n      if (condition.operator === "all_odd") return sample.length > 0 && sample.every((digit) => digit % 2 === 1);\n      return sample.every((digit) => compare(digit, condition.operator, Number(condition.value)));`,
  );
}

engine = ensureOnce(
  engine,
  "active primary/after-loss strategy route",
  "function activeExecutionRoute()",
  `  function strategyMatches(history) {\n    if (!state.strategy || !history?.ready) return false;\n    return state.strategy.conditions.every((condition) => conditionMatches(condition, history));\n  }`,
  `  function normalizeExecutionRoute(raw, routeKey = "primary") {\n    const source = raw && typeof raw === "object" ? raw : {};\n    let tradeType = String(source.trade_type || source.side || "").toLowerCase();\n    if (tradeType === "higher") tradeType = "rise";\n    if (tradeType === "lower") tradeType = "fall";\n    if (!CONTRACT_TYPES[tradeType]) return null;\n    const conditions = Array.isArray(source.conditions) ? source.conditions.map(normalizeCondition) : [];\n    if (!conditions.length) return null;\n    const prediction = ["over", "under", "matches", "differs"].includes(tradeType)\n      ? clampInt(source.prediction, 0, 9, 0)\n      : null;\n    return {\n      route_key: routeKey,\n      trade_type: tradeType,\n      prediction,\n      duration_ticks: clampInt(source.duration_ticks, 1, 100, 1),\n      conditions,\n    };\n  }\n\n  function activeExecutionRoute() {\n    const primary = state.strategy ? normalizeExecutionRoute(state.strategy, "primary") : null;\n    if (!primary) return null;\n    const routing = state.strategy?.result_routing;\n    if (state.recoveryDebt > 0.009 && routing?.enabled && routing?.after_loss) {\n      const recovery = normalizeExecutionRoute(routing.after_loss, "after_loss");\n      if (recovery) return recovery;\n    }\n    return primary;\n  }\n\n  function strategyMatches(history, route = activeExecutionRoute()) {\n    if (!route || !history?.ready) return false;\n    return route.conditions.every((condition) => conditionMatches(condition, history));\n  }`,
);

engine = ensureOnce(
  engine,
  "proposal uses active execution route",
  "function proposalRequest(symbol, stake, route = activeExecutionRoute())",
  `  function proposalRequest(symbol, stake) {\n    const strategy = state.strategy;\n    const request = {\n      proposal: 1,\n      amount: Math.round(stake * 100) / 100,\n      basis: "stake",\n      contract_type: CONTRACT_TYPES[strategy.trade_type],\n      currency: String(state.account?.currency || "USD").toUpperCase(),\n      duration: Number(strategy.duration_ticks || 1),\n      duration_unit: "t",\n      underlying_symbol: symbol,\n    };\n    if (["over", "under", "matches", "differs"].includes(strategy.trade_type)) {\n      request.barrier = String(strategy.prediction);\n    }\n    return request;\n  }`,
  `  function proposalRequest(symbol, stake, route = activeExecutionRoute()) {\n    const strategy = route || activeExecutionRoute();\n    if (!strategy) throw new Error("No active execution route");\n    const request = {\n      proposal: 1,\n      amount: Math.round(stake * 100) / 100,\n      basis: "stake",\n      contract_type: CONTRACT_TYPES[strategy.trade_type],\n      currency: String(state.account?.currency || "USD").toUpperCase(),\n      duration: Number(strategy.duration_ticks || 1),\n      duration_unit: "t",\n      underlying_symbol: symbol,\n    };\n    if (["over", "under", "matches", "differs"].includes(strategy.trade_type)) {\n      request.barrier = String(strategy.prediction);\n    }\n    return request;\n  }`,
);

engine = ensureOnce(
  engine,
  "executeReal route snapshot",
  "async function executeReal(symbol, history, route = activeExecutionRoute())",
  `  async function executeReal(symbol, history) {\n    if (!state.running || state.ownerLost || state.inFlight || state.openContracts.size) return;`,
  `  async function executeReal(symbol, history, route = activeExecutionRoute()) {\n    if (!route || !state.running || state.ownerLost || state.inFlight || state.openContracts.size) return;`,
);

engine = ensureCount(
  engine,
  "proposal call route snapshot",
  `sendRequest("private", proposalRequest(symbol, stake), 4500)`,
  `sendRequest("private", proposalRequest(symbol, stake, route), 4500)`,
  2,
);

engine = ensureOnce(
  engine,
  "journal and open-contract route identity",
  "tradeType: route.trade_type",
  `        tradeType: state.strategy.trade_type,\n        prediction: state.strategy.prediction,`,
  `        tradeType: route.trade_type,\n        prediction: route.prediction,\n        routeKey: route.route_key,`,
);

engine = ensureOnce(
  engine,
  "open journal route identity",
  "route_key: route.route_key",
  `        trade_type: state.strategy.trade_type,\n        prediction: state.strategy.prediction,\n        stake,`,
  `        trade_type: route.trade_type,\n        prediction: route.prediction,\n        route_key: route.route_key,\n        stake,`,
);

engine = ensureOnce(
  engine,
  "virtual route snapshot",
  "function beginVirtual(symbol, history, route = activeExecutionRoute())",
  `  function beginVirtual(symbol, history) {\n    if (Date.now() < Number(state.virtualNextEntryAtBySymbol.get(symbol) || 0)) return;\n    const latest = history.quotes[history.quotes.length - 1];\n    state.virtualPending = {\n      id: \`virtual-\${Date.now()}-\${Math.random().toString(16).slice(2)}\`,\n      symbol,\n      tradeType: state.strategy.trade_type,\n      prediction: state.strategy.prediction,\n      entryQuote: latest,\n      remaining: Math.max(1, Number(state.strategy.duration_ticks || 1)),\n    };\n    appendJournal({\n      mode: "virtual",\n      state: "OPEN",\n      contract_id: state.virtualPending.id,\n      symbol,\n      trade_type: state.strategy.trade_type,\n      prediction: state.strategy.prediction,\n      entry_quote: latest,\n      stake: 0,\n      profit: 0,\n      amount_charged: 0,\n    });\n    updateStatus("Direct • virtual protection observation running • $0 charged");\n  }`,
  `  function beginVirtual(symbol, history, route = activeExecutionRoute()) {\n    if (!route || state.virtualPending) return;\n    if (Date.now() < Number(state.virtualNextEntryAtBySymbol.get(symbol) || 0)) return;\n    const latest = history.quotes[history.quotes.length - 1];\n    state.virtualPending = {\n      id: \`virtual-\${Date.now()}-\${Math.random().toString(16).slice(2)}\`,\n      symbol,\n      tradeType: route.trade_type,\n      prediction: route.prediction,\n      routeKey: route.route_key,\n      entryQuote: latest,\n      remaining: Math.max(1, Number(route.duration_ticks || 1)),\n    };\n    appendJournal({\n      mode: "virtual",\n      state: "OPEN",\n      contract_id: state.virtualPending.id,\n      symbol,\n      trade_type: route.trade_type,\n      prediction: route.prediction,\n      route_key: route.route_key,\n      entry_quote: latest,\n      stake: 0,\n      profit: 0,\n      amount_charged: 0,\n    });\n    updateStatus("Direct • virtual protection observation running • $0 charged");\n  }`,
);

engine = ensureOnce(
  engine,
  "per-tick active route selection",
  "if (state.virtualMode) beginVirtual(symbol, history, route);",
  `    if (!strategyMatches(history)) return;\n    if (state.virtualMode) beginVirtual(symbol, history);\n    else executeReal(symbol, history);`,
  `    const route = activeExecutionRoute();\n    if (!route || !strategyMatches(history, route)) return;\n    if (state.virtualMode) beginVirtual(symbol, history, route);\n    else executeReal(symbol, history, route);`,
);

engine = ensureOnce(
  engine,
  "direct engine exported active route",
  "active_route: activeExecutionRoute()",
  `        strategy: state.strategy,`,
  `        strategy: state.strategy,\n        active_route: activeExecutionRoute(),`,
);

fs.writeFileSync(engineOut, engine, "utf8");

let ux = fs.readFileSync(uxSourcePath, "utf8").replace(/\r\n/g, "\n");
ux = ensureOnce(
  ux,
  "UX effective after-loss route helper",
  "function effectiveStrategy()",
  `  function activeStrategy() {\n    return runtime().strategy || null;\n  }`,
  `  function activeStrategy() {\n    return runtime().strategy || null;\n  }\n\n  function effectiveStrategy() {\n    const current = runtime();\n    const base = current.strategy;\n    const route = current.active_route;\n    if (!base || !route || route.route_key !== "after_loss") return base;\n    return {\n      ...base,\n      trade_type: route.trade_type,\n      prediction: route.prediction,\n      duration_ticks: route.duration_ticks,\n      conditions: Array.isArray(route.conditions) ? route.conditions : base.conditions,\n      __route_label: "After-loss recovery",\n    };\n  }`,
);

ux = ensureOnce(
  ux,
  "UX route-change state",
  `let lastRouteKey = "primary";`,
  `  let latestLive = null;\n  let lastExecution = "";`,
  `  let latestLive = null;\n  let lastExecution = "";\n  let lastRouteKey = "primary";`,
);

ux = ensureOnce(
  ux,
  "UX tick evaluator uses active route",
  "const routeKey = String(current.active_route?.route_key || \"primary\");",
  `    const current = runtime();\n    const s = current.strategy;\n    if (!current.running || !s || !Array.isArray(s.markets) || !s.markets.includes(symbol)) return;`,
  `    const current = runtime();\n    const routeKey = String(current.active_route?.route_key || "primary");\n    if (routeKey !== lastRouteKey) {\n      lastRouteKey = routeKey;\n      latestLive = null;\n      marketResults.clear();\n    }\n    const s = effectiveStrategy();\n    if (!current.running || !s || !Array.isArray(s.markets) || !s.markets.includes(symbol)) return;`,
);

ux = ensureOnce(
  ux,
  "UX strategy card uses active route",
  "const s = effectiveStrategy();\n    if (!s) return",
  `    const current = runtime();\n    const s = current.strategy;\n    if (!s) return`,
  `    const current = runtime();\n    const s = effectiveStrategy();\n    if (!s) return`,
);

ux = ensureOnce(
  ux,
  "UX card identifies recovery route",
  "const scopeBase = markets.length === 10",
  `    const scope = markets.length === 10 ? "Analyzing all 10 markets" : \`Analyzing \${markets.length} market\${markets.length === 1 ? "" : "s"}\`;`,
  `    const scopeBase = markets.length === 10 ? "Analyzing all 10 markets" : \`Analyzing \${markets.length} market\${markets.length === 1 ? "" : "s"}\`;\n    const scope = s.__route_label ? \`\${s.__route_label} · \${scopeBase}\` : scopeBase;`,
);

fs.writeFileSync(uxOut, ux, "utf8");

console.log(`Built ${path.relative(root, engineOut)} with result-route parity`);
console.log(`Built ${path.relative(root, uxOut)} with active-route MET/NOT-MET parity`);
