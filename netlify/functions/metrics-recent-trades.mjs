import { readSnapshot, json, proxyBackend } from "./shared.mjs";

export default async (request) => {
  try {
    return await proxyBackend(request, "/metrics/recent-trades");
  } catch (_) {
    const snapshot = await readSnapshot();
    const url = new URL(request.url);
    const limit = Math.max(1, Math.min(Number(url.searchParams.get("limit") || 30), 200));
    return json({ trades: (snapshot.trades || []).slice(0, limit) });
  }
};
