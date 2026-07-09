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
      regime_guard_paused: false,
      regime_guard_reason: "",
      regime_guard_updated_at: "",
      shadow_latest_samples: 0,
      shadow_latest_wins: 0,
      shadow_latest_losses: 0,
      shadow_latest_win_rate: 0,
      shadow_required_win_rate: 0.6,
      ai_activity_mode: "idle",
      ai_activity_label: "Standby",
      ai_activity_message: "Ai trading stopped",
      ai_activity_detail: "Waiting for synced local bot data.",
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

export function getBackendUrl() {
  const url = process.env.API_BASE_URL;
  if (!url) {
    throw new Error("API_BASE_URL environment variable is not set in Netlify");
  }
  return url.replace(/\/+$/, "");
}

export function cloneResponseHeaders(response) {
  const headers = new Headers(response.headers);
  const getSetCookie = response.headers.getSetCookie;
  const setCookies =
    typeof getSetCookie === "function" ? getSetCookie.call(response.headers) : [];
  if (setCookies.length) {
    headers.delete("set-cookie");
    for (const cookie of setCookies) {
      headers.append("set-cookie", cookie);
    }
  }
  return headers;
}

export async function proxyBackend(request, path) {
  const backendUrl = getBackendUrl();
  const incomingUrl = new URL(request.url);
  const targetUrl = new URL(path, backendUrl);
  incomingUrl.searchParams.forEach((value, key) => {
    targetUrl.searchParams.append(key, value);
  });

  const requestHeaders = new Headers();
  for (const header of ["cookie", "content-type", "authorization", "x-api-key"]) {
    const value = request.headers.get(header);
    if (value) {
      requestHeaders.set(header, value);
    }
  }
  requestHeaders.set("x-forwarded-proto", incomingUrl.protocol.replace(":", ""));
  requestHeaders.set("x-forwarded-host", incomingUrl.host);

  const init = {
    method: request.method,
    headers: requestHeaders,
    redirect: "manual",
  };
  if (!["GET", "HEAD"].includes(request.method.toUpperCase())) {
    init.body = await request.text();
  }

  const response = await fetch(targetUrl.toString(), init);
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: cloneResponseHeaders(response),
  });
}
