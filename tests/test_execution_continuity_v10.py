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
        self.assertIn("DERIVADMIN_DIRECT_PIP_PRECISION_V1?.last_digit", finalizer)
        self.assertIn("row?.actual_last_digit ?? row?.exit_digit", finalizer)
        self.assertIn('"spotDigit(row.entry_spot, row.symbol, row.entry_digit)"', finalizer)
        self.assertIn('explicit !== null && explicit !== undefined && explicit !== ""', finalizer)
        self.assertIn('"spotDigit(row.exit_spot, row.symbol, row.actual_last_digit)"', finalizer)

    def test_browser_direct_uses_pip_precision_for_digit_display_and_settlement(self) -> None:
        precision = self.text("dashboard/direct-pip-precision-v1.js")
        engine = self.text("dashboard/deriv-direct-execution-v1.js")
        self.assertIn("function lastDigit", precision)
        self.assertIn("numeric.toFixed(pip)", precision)
        self.assertIn("DEFAULT_PIP_BY_SYMBOL", precision)
        self.assertIn('active_symbols: "brief"', precision)
        self.assertIn('message?.msg_type === "active_symbols"', precision)
        for symbol, pip in {
            "1HZ10V": 4,
            "1HZ25V": 4,
            "1HZ50V": 4,
            "1HZ75V": 2,
            "1HZ100V": 2,
            "R_10": 2,
            "R_25": 2,
            "R_50": 2,
            "R_75": 2,
            "R_100": 2,
        }.items():
            self.assertIn(f'"{symbol}": {pip}', precision)
        self.assertIn("const explicitPip = validPip(explicitPipSize)", precision)
        self.assertIn("const pip = explicitPip !== null ? explicitPip : getPipSize(symbol)", precision)
        self.assertIn("precision: getPipSize", precision)
        self.assertIn("pip_size: getPipSize", precision)
        self.assertIn("last_digit: lastDigit", precision)
        self.assertIn('const direct = side === "entry" ? contract?.entry_spot : contract?.exit_spot', engine)
        self.assertIn("tick?.tick_display_value", engine)
        self.assertIn('contractSpot(contract, "entry")', engine)
        self.assertIn('contractSpot(contract, "exit")', engine)
        self.assertIn("entry_digit: entryDigit", engine)
        self.assertIn("actual_last_digit: exitDigit", engine)
        self.assertIn("provider_settlement: true", engine)
        self.assertNotIn("exit_spot: exitSpot ?? contract?.current_spot", engine)

    def test_start_flow_survives_shell_rerender_without_second_human_attempt(self) -> None:
        finalizer = self.text("scripts/finalize-execution-continuity-v1.mjs")
        self.assertIn("replacementStartTarget", finalizer)
        self.assertIn("target.isConnected ? target : replacementStartTarget(target)", finalizer)
        self.assertIn("approvedOnce.add(liveTarget)", finalizer)
        self.assertIn("liveTarget.click()", finalizer)

    def test_browser_direct_balance_updates_from_purchase_and_settlement_events(self) -> None:
        engine = self.text("dashboard/deriv-direct-execution-v1.js")
        runtime_ux = self.text("dashboard/direct-runtime-ux-v3.js")
        self.assertIn("function emitBalanceUpdate", engine)
        self.assertIn("derivadmin:direct-balance-live", engine)
        self.assertIn("buy.balance_after", engine)
        self.assertIn("emitBalanceUpdate({ delta: -buyPrice", engine)
        self.assertIn("contract?.balance_after", engine)
        self.assertIn("contract?.sell_price ?? contract?.payout", engine)
        self.assertIn("emitBalanceUpdate({ delta: Math.max(0, credited)", engine)
        self.assertIn('window.addEventListener("derivadmin:direct-balance-live"', runtime_ux)
        self.assertIn("providerBalance = Number(providerBalance ?? selectedAccount()?.balance ?? 0) + delta", runtime_ux)

    def test_finalizer_runs_after_scheduler_and_after_copied_execution_assets(self) -> None:
        docker = self.text("Dockerfile.frontend")
        scheduler = docker.index("node scripts/finalize-scheduler-v2.mjs")
        continuity = docker.index("node scripts/finalize-execution-continuity-v1.mjs")
        copied_run = docker.index("cp dashboard/direct-run-panel-authority-v6.js")
        assets = self.text("scripts/inject-frontend-assets.mjs")
        self.assertGreater(scheduler, copied_run)
        self.assertGreater(continuity, scheduler)
        self.assertIn("20260819-provider-settlement-v9", assets)
        self.assertIn("20260820-exit-digit-v12", assets)
        self.assertIn("20260818-interaction-v4-one-flow", assets)
        self.assertIn("20260819-live-fix-v2", assets)


if __name__ == "__main__":
    unittest.main()
