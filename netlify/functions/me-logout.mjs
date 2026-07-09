import { proxyBackend } from "./shared.mjs";

export default async (request) => {
  try {
    return await proxyBackend(request, "/me/logout");
  } catch (err) {
    return new Response(`Logout Proxy Error: ${err.message}`, { status: 500 });
  }
};
