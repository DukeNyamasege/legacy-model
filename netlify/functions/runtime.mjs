import { readSnapshot, json, proxyBackend } from "./shared.mjs";

export default async (request) => {
  try {
    return await proxyBackend(request, "/runtime");
  } catch (_) {
    const snapshot = await readSnapshot();
    return json(snapshot.runtime || { local_control: false, read_only_dashboard: true });
  }
};
