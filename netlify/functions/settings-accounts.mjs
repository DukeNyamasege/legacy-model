import { readSnapshot, json, proxyBackend } from "./shared.mjs";

export default async (request) => {
  try {
    return await proxyBackend(request, "/settings/accounts");
  } catch (_) {
    const snapshot = await readSnapshot();
    return json({
      mode: snapshot?.summary?.mode || "demo",
      accounts: snapshot.accounts || snapshot?.summary?.accounts || [],
      token_storage_secure: true,
      read_only_dashboard: true,
    });
  }
};
