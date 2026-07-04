import { readSnapshot, json } from "./shared.mjs";

export default async () => {
  const snapshot = await readSnapshot();
  return json({
    mode: snapshot?.summary?.mode || "demo",
    accounts: snapshot.accounts || snapshot?.summary?.accounts || [],
    token_storage_secure: true,
    read_only_dashboard: true,
  });
};
