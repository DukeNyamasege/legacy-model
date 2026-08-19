from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExecutionContinuityV10Contract(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_browser_runtime_repairs_stale_sockets_and_open_contract_subscription(self) -> None:
        finalizer = self.text("scripts/finalize-execution-continuity-v1.mjs")
        for marker in (
            "restoreOpenContractSubscriptions",
            "continuityRepair",
            "forceSocketReconnect",
            'proposal_open_contract: 1, contract_id: numeric, subscribe: 1',
            'state.continuityTimer = setInterval(continuityRepair, 2000)',
            'market stream became stale',
            'secure trade stream became stale',
            'settlement repair',
            'last_tick_age_ms',
        ):
            self.assertIn(marker, finalizer)
        self.assertIn("ws.onopen = () => {\\n        state.publicConnectPromise = null;", finalizer)
        self.assertIn("state.lastPublicMessageAt = Date.now();", finalizer)
        self.assertIn("state.lastTickAt = Date.now();", finalizer)

    def test_late_frontend_finalizers_normalize_newlines_before_anchor_matching(self) -> None:
        for path in (
            "scripts/finalize-scheduler-v2.mjs",
            "scripts/finalize-execution-continuity-v1.mjs",
        ):
            self.assertIn('.replace(/\\r\\n/g, "\\n")', self.text(path))

    def test_runtime_never_uses_reset_to_mutate_financial_state(self) -> None:
        finalizer = self.text("scripts/finalize-execution-continuity-v1.mjs")
        reset_patch = finalizer.split("reset history only", 1)[0].split("Reset is a history action only", 1)[1]
        self.assertIn("Financial execution state deliberately survives Reset", reset_patch)
        self.assertNotIn("state.sessionProfit = 0", reset_patch.split("`  function clearLocalTrades()", 2)[-1])
        self.assertIn("window.__DERIVADMIN_RESET_PENDING_UNTIL", finalizer)
        self.assertNotIn('window.confirm(\\"Do you want to reset all trades?\\")', finalizer.split("one-click reset", 1)[1])

    def test_virtual_hook_is_final_pre_buy_fence_and_virtual_rows_are_visible(self) -> None:
        finalizer = self.text("scripts/finalize-execution-continuity-v1.mjs")
        for marker in (
            "virtualHookShouldProtect",
            "consecutive ACTUAL losses",
            "another real BUY is impossible",
            "beginVirtual(symbol, history, route)",
            "state.consecutiveLosses = 0",
            "VIRTUAL · ${typeLabel(row)}",
            "VIRTUAL ${String(row.outcome",
            "actualRows = rows.filter",
            "actual-only financial KPI loop",
        ):
            self.assertIn(marker, finalizer)
        runtime = self.text("dashboard/deriv-direct-execution-v1.js")
        self.assertIn("entry_quote: pending.entryQuote", runtime)
        self.assertIn("exit_quote: history.quotes[history.quotes.length - 1]", runtime)
        self.assertIn("virtualNextEntryAtBySymbol: new Map()", runtime)
        self.assertIn("state.virtualNextEntryAtBySymbol.set(pending.symbol, Date.now() + 3000)", runtime)
        self.assertIn("Date.now() < Number(state.virtualNextEntryAtBySymbol.get(symbol) || 0)", runtime)
        self.assertIn("if (state.virtualPending && advanceVirtual(symbol, history)) return;", runtime)
        self.assertIn("return true;", runtime.split("function advanceVirtual", 1)[1])
        compiler = self.text("scripts/build-direct-runtime-v2.mjs")
        self.assertIn("entry_quote: latest", compiler)
        self.assertIn("virtualNextEntryAtBySymbol.get(symbol)", compiler)
        self.assertIn("row?.entry_quote", finalizer)
        self.assertIn("row?.exit_quote", finalizer)
        self.assertIn("function spotDigit", finalizer)
        self.assertIn("const entry = spotDigit(row.entry_spot)", finalizer)
        self.assertIn("const exit = settled ? (spotDigit(row.exit_spot)", finalizer)

    def test_start_flow_survives_shell_rerender_without_second_human_attempt(self) -> None:
        finalizer = self.text("scripts/finalize-execution-continuity-v1.mjs")
        self.assertIn("replacementStartTarget", finalizer)
        self.assertIn("target.isConnected ? target : replacementStartTarget(target)", finalizer)
        self.assertIn("approvedOnce.add(liveTarget)", finalizer)
        self.assertIn("liveTarget.click()", finalizer)

    def test_finalizer_runs_after_scheduler_and_after_copied_execution_assets(self) -> None:
        docker = self.text("Dockerfile.frontend")
        scheduler = docker.index("node scripts/finalize-scheduler-v2.mjs")
        continuity = docker.index("node scripts/finalize-execution-continuity-v1.mjs")
        copied_run = docker.index("cp dashboard/direct-run-panel-authority-v6.js")
        self.assertGreater(scheduler, copied_run)
        self.assertGreater(continuity, scheduler)
        self.assertIn("20260819-browser-direct-v8-global-recovery", docker)
        self.assertIn("20260818-unified-ledger-v10-virtual", docker)
        self.assertIn("20260818-interaction-v4-one-flow", docker)
        self.assertIn("20260819-run-reset-global-recovery-v4", docker)


if __name__ == "__main__":
    unittest.main()
