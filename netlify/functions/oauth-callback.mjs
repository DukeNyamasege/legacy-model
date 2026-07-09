import { getBackendUrl } from "./shared.mjs";

export default async (request, context) => {
  try {
    const backendUrl = getBackendUrl();
    const url = new URL(request.url);
    const targetUrl = new URL(`/oauth/callback${url.search}`, backendUrl);
    
    // We need to forward the cookies from the request (OAuth state and verifier)
    const requestHeaders = new Headers();
    const cookie = request.headers.get("cookie");
    if (cookie) {
      requestHeaders.set("cookie", cookie);
    }
    
    // Add x-forwarded-proto so FastAPI knows it's https
    requestHeaders.set("x-forwarded-proto", url.protocol.replace(":", ""));
    requestHeaders.set("x-forwarded-host", url.host);

    const response = await fetch(targetUrl.toString(), {
      method: "GET",
      headers: requestHeaders,
      redirect: "manual"
    });

    const responseHeaders = new Headers(response.headers);
    // If the backend wants to clear cookies (e.g. Set-Cookie: deriv_oauth_state=...), 
    // it will be passed along to the browser automatically by the responseHeaders
    
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders
    });
  } catch (err) {
    return new Response(`OAuth Callback Proxy Error: ${err.message}`, { status: 500 });
  }
};
