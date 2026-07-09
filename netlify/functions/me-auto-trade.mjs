import { proxyBackend } from "./shared.mjs";

export default async (request) => {
  try {
    return await proxyBackend(request, "/me/auto-trade");
  } catch (err) {
    return new Response(`Auto Trading Proxy Error: ${err.message}`, { status: 500 });
  }
};
