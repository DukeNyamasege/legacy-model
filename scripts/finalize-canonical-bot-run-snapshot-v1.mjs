import fs from "node:fs";

const shellPath = "dist/final-ui-shell-v2.js";
const enginePath = "dist/deriv-direct-execution-v2.js";
const indexPath = "dist/index.html";

function read(path) {
  if (!fs.existsSync(path)) throw new Error(`canonical-bot-run-snapshot-v1 missing build artifact: ${path}`);
  return fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function write(path, source) {
  fs.writeFileSync(path, source, "utf8");
}

function replaceOne(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`canonical-bot-run-snapshot-v1 ${label}: expected 1 source match or installed shape, got ${count}`);
  return source.replace(before, after);
}

let shell = read(shellPath);
let engine = read(enginePath);
let index = read(indexPath);

// ---------------------------------------------------------------------------
// 1. Loading a bot must preserve the bot's own market scope. Historically the
//    helper named allMarketBuilder() silently converted every loaded bot to all
//    markets. Keep the function name for compatibility, but make it normalization
//    only so built-in and local bots retain their authored market mode/selection.
// ---------------------------------------------------------------------------
shell = replaceOne(
  shell,
  `  function allMarketBuilder(builder) {\n    const normalized = normalizeBuilderDraft(builder || {});\n    return normalizeBuilderDraft({\n      ...normalized,\n      marketMode: "all",\n      markets: supportedMarkets(),\n      market: supportedMarkets()[0] || normalized.market,\n    });\n  }`,
  `  function allMarketBuilder(builder) {\n    // Compatibility name only: loading a bot must never rewrite its authored\n    // market scope. normalizeBuilderDraft() already canonicalizes single,\n    // selected and all-market strategies.\n    return normalizeBuilderDraft(builder || {});\n  }`,
  "preserve loaded bot market scope",
);

// ---------------------------------------------------------------------------
// 2. An after-loss contract route is financial behavior and must be explicitly
//    enabled. The presence of a legacy after_loss object by itself must never turn
//    routing on. This prevents an old Over 4 recovery route from appearing inside
//    a bot that the trader currently intends to run as Over 3 only.
// ---------------------------------------------------------------------------
shell = replaceOne(
  shell,
  `    const resultRaw = source.resultRouting || source.result || source.result_routing || custom.result_routing || {};\n    const serverRoute = resultRaw.after_loss ? routeFromServer(resultRaw.after_loss, draftBase) : null;\n    const afterLoss = resultRaw.afterLoss ? normalizeRoute(resultRaw.afterLoss, draftBase) : serverRoute;\n    draftBase.resultRouting = {\n      enabled: Boolean(resultRaw.enabled ?? resultRaw.routingEnabled ?? afterLoss),\n      afterLoss,\n    };`,
  `    const resultRaw = source.resultRouting || source.result || source.result_routing || custom.result_routing || {};\n    const serverRoute = resultRaw.after_loss ? routeFromServer(resultRaw.after_loss, draftBase) : null;\n    const afterLoss = resultRaw.afterLoss ? normalizeRoute(resultRaw.afterLoss, draftBase) : serverRoute;\n    const explicitResultRouting = (resultRaw.enabled ?? resultRaw.routingEnabled) === true;\n    draftBase.resultRouting = {\n      enabled: explicitResultRouting,\n      afterLoss: explicitResultRouting ? afterLoss : null,\n    };`,
  "explicit after-loss routing only",
);

// ---------------------------------------------------------------------------
// 3. Every editable Builder field must immediately update selectedStrategy. The
//    Run button already serializes the live Builder DOM while Builder is open;
//    this additional synchronization guarantees that edits remain canonical if
//    the trader navigates away before pressing Run.
// ---------------------------------------------------------------------------
shell = replaceOne(
  shell,
  `    root.querySelectorAll("input, textarea, select").forEach((field) => {\n      field.addEventListener("focus", markEditing);\n      field.addEventListener("input", markEditing);\n      field.addEventListener("keydown", markEditing);\n      field.addEventListener("change", markEditing);\n    });`,
  `    root.querySelectorAll("input, textarea, select").forEach((field) => {\n      field.addEventListener("focus", markEditing);\n      field.addEventListener("input", markEditing);\n      field.addEventListener("keydown", markEditing);\n      field.addEventListener("change", markEditing);\n    });\n    root.querySelectorAll(".restored-builder #b-name,.restored-builder [data-builder],.restored-builder [data-result-route]").forEach((field) => {\n      const eventName = field.tagName === "SELECT" || ["checkbox", "radio"].includes(String(field.type || "")) ? "change" : "input";\n      field.addEventListener(eventName, () => {\n        try {\n          state.selectedStrategy = {\n            ...(state.selectedStrategy || {}),\n            builder: builderDraftFromDom(),\n          };\n        } catch (_) {}\n      });\n    });`,
  "persist every builder edit into current strategy",
);

// ---------------------------------------------------------------------------
// 4. The exact strategy supplied at Run is immutable for that run. Quiet UI
//    refreshes poll /me/custom-strategy, and those historical/server responses
//    must not replace the browser-direct execution snapshot while trading is on.
// ---------------------------------------------------------------------------
engine = replaceOne(
  engine,
  `    if (path === "/me/custom-strategy") {\n      response.clone().json().then((payload) => {\n        if (payload?.config?.configured) cacheStrategy({`,
  `    if (path === "/me/custom-strategy") {\n      response.clone().json().then((payload) => {\n        if (state.running) return;\n        if (payload?.config?.configured) cacheStrategy({`,
  "ignore stale custom-strategy refresh during active run",
);

engine = replaceOne(
  engine,
  `    if (path === "/me/custom-strategy" && method === "POST") {\n      const body = await requestBodyJson(input, init);\n      const strategy = cacheStrategy(body);`,
  `    if (path === "/me/custom-strategy" && method === "POST") {\n      const body = await requestBodyJson(input, init);\n      // Saving/editing during a live run may prepare the next strategy, but it\n      // cannot mutate the strategy already executing.\n      const strategy = state.running ? normalizeStrategy(body) : cacheStrategy(body);`,
  "do not mutate active run on strategy save",
);

// Cache-bust the two authorities changed by this finalizer.
index = index.replace(/final-ui-shell-v2\.js\?v=[^"']+/g, "final-ui-shell-v2.js?v=20260821-canonical-bot-snapshot-v1");
index = index.replace(/deriv-direct-execution-v2\.js\?v=[^"']+/g, "deriv-direct-execution-v2.js?v=20260821-canonical-bot-snapshot-v1");

// Fail closed if any old behavior survives the production artifact.
for (const forbidden of [
  `enabled: Boolean(resultRaw.enabled ?? resultRaw.routingEnabled ?? afterLoss)`,
  `marketMode: "all",\n      markets: supportedMarkets(),\n      market: supportedMarkets()[0] || normalized.market`,
]) {
  if (shell.includes(forbidden)) throw new Error(`canonical-bot-run-snapshot-v1 stale bot behavior survived: ${forbidden}`);
}
for (const required of [
  `const explicitResultRouting = (resultRaw.enabled ?? resultRaw.routingEnabled) === true;`,
  `afterLoss: explicitResultRouting ? afterLoss : null`,
  `return normalizeBuilderDraft(builder || {});`,
  `.restored-builder #b-name,.restored-builder [data-builder],.restored-builder [data-result-route]`,
]) {
  if (!shell.includes(required)) throw new Error(`canonical-bot-run-snapshot-v1 shell invariant missing: ${required}`);
}
for (const required of [
  `if (state.running) return;`,
  `const strategy = state.running ? normalizeStrategy(body) : cacheStrategy(body);`,
  `function activeExecutionRoute()`,
  `routing?.enabled && routing?.after_loss`,
]) {
  if (!engine.includes(required)) throw new Error(`canonical-bot-run-snapshot-v1 engine invariant missing: ${required}`);
}
for (const required of [
  "final-ui-shell-v2.js?v=20260821-canonical-bot-snapshot-v1",
  "deriv-direct-execution-v2.js?v=20260821-canonical-bot-snapshot-v1",
]) {
  if (!index.includes(required)) throw new Error(`canonical-bot-run-snapshot-v1 cache-bust missing: ${required}`);
}

write(shellPath, shell);
write(enginePath, engine);
write(indexPath, index);
console.log("CANONICAL_BOT_RUN_SNAPSHOT_V1_INSTALLED preserve_bot_scope=true explicit_after_loss_only=true current_builder_edits=true active_run_immutable=true");
