import fs from "node:fs";

const path = "dist/final-ui-shell-v2.js";
let source = fs.readFileSync(path, "utf8").replace(/\r\n/g, "\n");

if (!source.includes("function exactStrategyPreview()")) {
  const before = `  window.FOA_FINAL_UI = Object.freeze({ version: "20260818-local-ui-12", refresh, go });`;
  const after = `  function exactStrategyPreview() {\n    try {\n      if (state.route === "builder" && root.querySelector(".restored-builder")) return builderSnapshot();\n      if (state.route === "ready" && state.generated) {\n        const canonical = generatedCanonical();\n        if (canonical) return {\n          name: state.generated.name || state.generated.strategy_name || canonical.name || "AI Generated Strategy",\n          source: "ai",\n          strategy: canonical,\n        };\n      }\n      if (state.selectedStrategy?.strategy?.market_mode) return state.selectedStrategy;\n      if (state.custom?.config?.configured) return {\n        name: state.custom.config.name || state.custom.config.strategy_name || "Saved strategy",\n        source: "server",\n        strategy: state.custom.config,\n      };\n    } catch (_) {}\n    return null;\n  }\n\n  window.FOA_FINAL_UI = Object.freeze({ version: "20260818-local-ui-12", refresh, go, exactStrategyPreview });`;
  const count = source.split(before).length - 1;
  if (count !== 1) throw new Error(`prepare-exact-builder-preview expected one shell export, got ${count}`);
  source = source.replace(before, after);
}

if (!source.includes("exactStrategyPreview")) throw new Error("canonical Builder preview was not installed");
fs.writeFileSync(path, source, "utf8");
console.log("PREPARE_EXACT_BUILDER_PREVIEW_V1_INSTALLED canonical_builder_snapshot=true");
