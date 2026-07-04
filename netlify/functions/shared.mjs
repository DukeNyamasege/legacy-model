import { getStore } from "@netlify/blobs";

export function getDashboardStore() {
  // Build a fresh store client per invocation so warm functions don't keep
  // using an expired internal blobs token.
  return getStore("dashboard-state");
}

export async function readSnapshot() {
  const store = getDashboardStore();
  return (await store.get("latest", { type: "json" })) || {
    summary: {
      status: "OFFLINE",
      pause_reason: "No synced local bot data yet",
      total_traders: 0,
      purchased_trades: 0,
      wins: 0,
      losses: 0,
      longest_win_streak: 0,
      longest_loss_streak: 0,
      win_rate: 0,
      net_profit: 0,
      open_trades: 0,
      skipped_signals: 0,
      account_balance_total: 0,
      accounts: [],
      mode: "demo",
    },
    trades: [],
    accounts: [],
    runtime: {
      local_control: false,
      read_only_dashboard: true,
    },
    meta: {
      synced_at: null,
    },
  };
}

export function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}
