import { cloneResponseHeaders, getBackendUrl } from "./shared.mjs";

export default async (request, context) => {
  try {
    const backendUrl = getBackendUrl();
    const url = new URL(request.url);
    const targetUrl = new URL(`/oauth/start${url.search}`, backendUrl);
    
    // Forward the request to the Render backend
    const response = await fetch(targetUrl.toString(), {
      method: "GET",
      headers: {
        "x-forwarded-proto": new URL(request.url).protocol.replace(":", ""),
        "x-forwarded-host": new URL(request.url).host,
      },
      redirect: "manual" // We want to forward the redirect to the browser, not follow it
    });

    // Reconstruct the response headers, forwarding Set-Cookie
    const headers = cloneResponseHeaders(response);
    
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: headers
    });
  } catch (err) {
    return new Response(`OAuth Proxy Error: ${err.message}`, { status: 500 });
  }
};
