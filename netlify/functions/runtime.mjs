import { readSnapshot, json } from "./shared.mjs";

export default async () => {
  const snapshot = await readSnapshot();
  return json(snapshot.runtime || { local_control: false, read_only_dashboard: true });
};
