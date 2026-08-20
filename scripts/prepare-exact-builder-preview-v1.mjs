import fs from "node:fs";

const path = "dist/final-ui-shell-v2.js";
const guardPath = "dist/direct-interaction-guard-v3.js";
const enginePath = "dist/deriv-direct-execution-v2.js";
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

// Normalize and then wire the interaction guard itself to the canonical Builder
// snapshot. The modal and the execution engine therefore receive one payload,
// instead of independently reconstructing strategy values from rendered text.
let guard = fs.readFileSync(guardPath, "utf8").replace(/\r\n/g, "\n");
const inlineNameShape = `    const sl = Number(strategy?.execution_settings?.stop_loss);\n    return {\n      name: String(strategy.name || strategy.strategy_name || "Current strategy"),`;
const normalizedNameShape = `    const sl = Number(strategy?.execution_settings?.stop_loss);\n    const name = String(strategy.name || strategy.strategy_name || "Current strategy");\n    return {\n      name,`;
if (!guard.includes(normalizedNameShape) && !guard.includes("nameOverride || strategy.name")) {
  const count = guard.split(inlineNameShape).length - 1;
  if (count !== 1) {
    throw new Error(`prepare-exact-builder-preview expected one interaction-guard inline-name shape, got ${count}`);
  }
  guard = guard.replace(inlineNameShape, normalizedNameShape);
}

if (!guard.includes("  function focusableElements(overlay) {")) {
  const modalBoundary = "  function modal({ title, intro = \"\", body = \"\", confirmText = \"Proceed\", cancelText = \"Cancel\", danger = false }) {";
  const count = guard.split(modalBoundary).length - 1;
  if (count !== 1) {
    throw new Error(`prepare-exact-builder-preview expected one interaction-guard modal boundary, got ${count}`);
  }
  const focusable = `  function focusableElements(overlay) {\n    if (!overlay || typeof overlay.querySelectorAll !== "function") return [];\n    return Array.from(overlay.querySelectorAll("button,[href],input,select,textarea,[tabindex]:not([tabindex='-1'])"));\n  }\n\n`;
  guard = guard.replace(modalBoundary, focusable + modalBoundary);
}

if (!guard.includes("exactStrategyPreview?.()")) {
  const savedOld = `  function savedSummary() {\n    const strategy = runtime().strategy || {};`;
  const savedNew = `  function savedSummary(strategyOverride = null, nameOverride = "") {\n    const strategy = strategyOverride || runtime().strategy || {};`;
  const savedCount = guard.split(savedOld).length - 1;
  if (savedCount !== 1) {
    throw new Error(`prepare-exact-builder-preview expected one savedSummary source shape, got ${savedCount}`);
  }
  guard = guard.replace(savedOld, savedNew);

  const nameOld = `    const name = String(strategy.name || strategy.strategy_name || "Current strategy");`;
  const nameNew = `    const name = String(nameOverride || strategy.name || strategy.strategy_name || "Current strategy");`;
  const nameCount = guard.split(nameOld).length - 1;
  if (nameCount !== 1) {
    throw new Error(`prepare-exact-builder-preview expected one savedSummary name source shape, got ${nameCount}`);
  }
  guard = guard.replace(nameOld, nameNew);

  const summaryStart = `  function summaryFor(target) {`;
  const summaryEnd = `  function focusableElements(overlay) {`;
  const a = guard.indexOf(summaryStart);
  const b = a >= 0 ? guard.indexOf(summaryEnd, a + summaryStart.length) : -1;
  if (a < 0 || b < 0) {
    throw new Error("prepare-exact-builder-preview could not resolve summaryFor boundaries");
  }
  const summary = `  function summaryFor(target) {\n    try {\n      const exact = window.FOA_FINAL_UI?.exactStrategyPreview?.();\n      const exactStrategy = exact?.strategy || exact?.canonical || exact?.config || null;\n      if (exactStrategy?.market_mode) return savedSummary(exactStrategy, exact?.name || "");\n    } catch (_) {}\n\n    // Compatibility fallback only. Canonical Builder state is authoritative.\n    if (\n      target.closest(".builder-panel")\n      || target.closest(".builder-workspace")\n      || target.hasAttribute("data-builder-trade")\n    ) return builderSummary(target);\n    return savedSummary();\n  }\n\n`;
  guard = guard.slice(0, a) + summary + guard.slice(b);
}

for (const marker of [
  "exactStrategyPreview?.()",
  "savedSummary(strategyOverride = null, nameOverride = \"\")",
  "savedSummary(exactStrategy, exact?.name || \"\")",
  "  function focusableElements(overlay) {",
]) {
  if (!guard.includes(marker)) throw new Error(`canonical Builder confirmation marker missing: ${marker}`);
}

fs.writeFileSync(guardPath, guard, "utf8");

// The finalized browser engine may or may not already expose `prewarm`. The
// diagnostics finalizer installs its method immediately before state(), so keep
// that boundary deterministic. If prewarm is absent, expose the already-existing
// prewarmData function; if present elsewhere in the export, move only that member.
let engine = fs.readFileSync(enginePath, "utf8").replace(/\r\n/g, "\n");
const engineExportMarker = "window.DERIVADMIN_DIRECT_EXECUTION_V1 = Object.freeze({";
const engineExportStart = engine.indexOf(engineExportMarker);
const engineExportEnd = engineExportStart >= 0 ? engine.indexOf("});", engineExportStart + engineExportMarker.length) : -1;
if (engineExportStart < 0 || engineExportEnd < 0) {
  throw new Error("prepare-exact-builder-preview could not resolve browser execution export");
}
const prewarmLine = "    prewarm: prewarmData,\n";
const stateLine = "    state() {";
let engineExport = engine.slice(engineExportStart, engineExportEnd);
const stateAtRelative = engineExport.indexOf(stateLine);
if (stateAtRelative < 0) {
  throw new Error("prepare-exact-builder-preview browser execution state member missing");
}
if (!engineExport.includes(prewarmLine + stateLine)) {
  const prewarmAtRelative = engineExport.indexOf(prewarmLine);
  if (prewarmAtRelative >= 0) {
    const absolutePrewarm = engineExportStart + prewarmAtRelative;
    engine = engine.slice(0, absolutePrewarm) + engine.slice(absolutePrewarm + prewarmLine.length);
  }
  const refreshedExportStart = engine.indexOf(engineExportMarker);
  const refreshedState = engine.indexOf(stateLine, refreshedExportStart + engineExportMarker.length);
  if (refreshedState < 0) throw new Error("prepare-exact-builder-preview browser execution state member disappeared during normalization");
  engine = engine.slice(0, refreshedState) + prewarmLine + engine.slice(refreshedState);
}
const normalizedExportStart = engine.indexOf(engineExportMarker);
const normalizedExportEnd = engine.indexOf("});", normalizedExportStart + engineExportMarker.length);
engineExport = engine.slice(normalizedExportStart, normalizedExportEnd);
if (!engineExport.includes(prewarmLine + stateLine)) {
  throw new Error("browser execution diagnostics export boundary was not normalized");
}
fs.writeFileSync(enginePath, engine, "utf8");

console.log("PREPARE_EXACT_BUILDER_PREVIEW_V1_INSTALLED canonical_builder_snapshot=true canonical_confirmation=true finalized_export_shape=preserved engine_diagnostics_boundary=normalized");
