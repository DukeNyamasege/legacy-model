import { proxyBackend } from "./shared.mjs";

export default async (request) => {
  try {
    return await proxyBackend(request, "/me");
  } catch (err) {
    return new Response(`Account Proxy Error: ${err.message}`, { status: 500 });
  }
};
