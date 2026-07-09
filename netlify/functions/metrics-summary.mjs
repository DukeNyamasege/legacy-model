import { readSnapshot, json, proxyBackend } from "./shared.mjs";

export default async (request) => {
  try {
    return await proxyBackend(request, "/metrics/summary");
  } catch (_) {
    const snapshot = await readSnapshot();
    return json(snapshot.summary || {});
  }
};
