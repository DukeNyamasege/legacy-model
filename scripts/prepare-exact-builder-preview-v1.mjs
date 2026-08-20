import fs from "node:fs";

const path = "dist/final-ui-shell-v2.js";
const guardPath = "dist/direct-interaction-guard-v3.js";
let source = fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");

const previewFunction = `  function exactStrategyPreview() {\n    try {\n      if (state.route === "builder" && root.querySelector(".restored-builder")) return builderSnapshot();\n      if (state.route === "ready" && state.generated) {\n        const canonical = generatedCanonical();\n        if (canonical) return {\n          name: state.generated.name || state.generated.strategy_name || canonical.name || "AI Generated Strategy",\n          source: "ai",\n          strategy: canonical,\n        };\n      }\n      if (state.selectedStrategy?.strategy?.market_mode) return state.selectedStrategy;\n      if (state.custom?.config?.configured) return {\n        name: state.custom.config.name || state.custom.config.strategy_name || "Saved strategy",\n        source: "server",\n        strategy: state.custom.config,\n      };\n    } catch (_) {}\n    return null;\n  }\n\n`;

if (!source.includes("function exactStrategyPreview()")) {
  const marker = "window.FOA_FINAL_UI = Object.freeze({";
  const starts = [];
  let cursor = 0;
  while (true) {
    const found = source.indexOf(marker, cursor);
    if (found < 0) break;
    starts.push(found);
    cursor = found + marker.length;
  }
  if (starts.length !== 1) {
    throw new Error(`prepare-exact-builder-preview expected one FOA_FINAL_UI export, got ${starts.length}`);
  }

  const exportStart = starts[0];
  const objectStart = source.indexOf("{", exportStart);
  const exportEnd = source.indexOf("});", objectStart + 1);
  if (objectStart < 0 || exportEnd < 0) {
    throw new Error("prepare-exact-builder-preview could not resolve the finalized FOA_FINAL_UI export boundary");
  }

  const lineStart = source.lastIndexOf("\n", exportStart - 1) + 1;
  const body = source.slice(objectStart + 1, exportEnd);
  if (body.includes("exactStrategyPreview")) {
    throw new Error("prepare-exact-builder-preview export already references exactStrategyPreview without its function");
  }
  const trimmed = body.trimEnd();
  const separator = trimmed.endsWith(",") ? " " : ", ";
  const upgradedExport = source.slice(exportStart, objectStart + 1)
    + body
    + separator
    + "exactStrategyPreview "
    + source.slice(exportEnd, exportEnd + 3);

  source = source.slice(0, lineStart)
    + previewFunction
    + source.slice(lineStart, exportStart)
    + upgradedExport
    + source.slice(exportEnd + 3);
}

if (!source.includes("function exactStrategyPreview()")) {
  throw new Error("canonical Builder preview function was not installed");
}
const exportMarker = "window.FOA_FINAL_UI = Object.freeze({";
const exportStart = source.indexOf(exportMarker);
const exportEnd = exportStart >= 0 ? source.indexOf("});", exportStart + exportMarker.length) : -1;
if (exportStart < 0 || exportEnd < 0 || !source.slice(exportStart, exportEnd).includes("exactStrategyPreview")) {
  throw new Error("canonical Builder preview is not exported by FOA_FINAL_UI");
}

fs.writeFileSync(path, source, "utf8");

// Normalize the interaction guard to the shape expected by the final exact-review
// authority. The source guard currently renders the strategy name inline inside
// the returned object, while the finalizer needs a local `name` variable so it can
// apply the canonical Builder name override. This is a build-only normalization;
// behavior is unchanged until the exact-review finalizer runs immediately after.
let guard = fs.readFileSync(guardPath, "utf8").replace(/\r\n/g, "\n");
const inlineNameShape = `    const sl = Number(strategy?.execution_settings?.stop_loss);\n    return {\n      name: String(strategy.name || strategy.strategy_name || "Current strategy"),`;
const normalizedNameShape = `    const sl = Number(strategy?.execution_settings?.stop_loss);\n    const name = String(strategy.name || strategy.strategy_name || "Current strategy");\n    return {\n      name,`;
if (!guard.includes(normalizedNameShape)) {
  const count = guard.split(inlineNameShape).length - 1;
  if (count !== 1) {
    throw new Error(`prepare-exact-builder-preview expected one interaction-guard inline-name shape, got ${count}`);
  }
  guard = guard.replace(inlineNameShape, normalizedNameShape);
}
if (!guard.includes('const name = String(strategy.name || strategy.strategy_name || "Current strategy");')) {
  throw new Error("interaction guard name normalization was not installed");
}
fs.writeFileSync(guardPath, guard, "utf8");

console.log("PREPARE_EXACT_BUILDER_PREVIEW_V1_INSTALLED canonical_builder_snapshot=true finalized_export_shape=preserved guard_shape=normalized");
