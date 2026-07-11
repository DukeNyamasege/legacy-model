import { cloneResponseHeaders, getBackendUrl } from "./shared.mjs";

export default async (request, context) => {
  try {
    const backendUrl = getBackendUrl();
    const targetUrl = new URL(`/oauth/start`, backendUrl);
    const response = await fetch(targetUrl.toString(), {
      method: "GET",
      headers: {
        "x-forwarded-proto": "https",
        "x-forwarded-host": new URL(request.url).host,
      },
      redirect: "manual"
    });
    const headers = cloneResponseHeaders(response);
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers
    });
  } catch (err) {
    return new Response(`OAuth Proxy Error: ${err.message}`, { status: 500 });
  }
};
