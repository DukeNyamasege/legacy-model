import { getStore } from "@netlify/blobs";

export function getDashboardStore() {
  return getStore("dashboard-state");
}

export async function readSnapshot() {
  const store = getDashboardStore();
  return (await store.get("latest", { type: "json" })) || {
    summary: {},
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
  for (const header of [
    "content-encoding",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
  ]) {
    headers.delete(header);
  }
  const getSetCookie = response.headers.getSetCookie;
  const setCookies =
    typeof getSetCookie === "function" ? getSetCookie.call(response.headers) : [];
  if (setCookies.length) {
    headers.delete("set-cookie");
    for (const cookie of setCookies) {
      headers.append("set-cookie", cookie);
    }
  }
  headers.set("cache-control", "no-store");
  return headers;
}
