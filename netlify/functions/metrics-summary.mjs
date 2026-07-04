import { readSnapshot, json } from "./shared.mjs";

export default async () => {
  const snapshot = await readSnapshot();
  return json(snapshot.summary || {});
};
