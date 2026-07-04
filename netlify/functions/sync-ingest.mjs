import { store, json } from "./shared.mjs";

export default async (request) => {
  if (request.method !== "POST") {
    return json({ error: "Method not allowed" }, 405);
  }

  const expected = (process.env.NETLIFY_SYNC_TOKEN || "").trim();
  const supplied = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "").trim();
  if (!expected || supplied !== expected) {
    return json({ error: "Unauthorized" }, 401);
  }

  const payload = await request.json().catch(() => null);
  if (!payload || typeof payload !== "object") {
    return json({ error: "Invalid JSON payload" }, 400);
  }

  await store.setJSON("latest", payload);
  return json({ ok: true, synced_at: payload?.meta?.synced_at || null });
};
