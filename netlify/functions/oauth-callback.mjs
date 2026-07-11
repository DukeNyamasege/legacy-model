import { cloneResponseHeaders, getBackendUrl } from "./shared.mjs";

export default async (request, context) => {
  try {
    const backendUrl = getBackendUrl();
    const url = new URL(request.url);
    const targetUrl = new URL(`/oauth/callback`, backendUrl);
    url.searchParams.forEach((value, key) => targetUrl.searchParams.append(key, value));

    const requestHeaders = new Headers();
    const cookie = request.headers.get("cookie");
    if (cookie) {
      requestHeaders.set("cookie", cookie);
    }
    requestHeaders.set("x-forwarded-proto", "https");
    requestHeaders.set("x-forwarded-host", url.host);

    const response = await fetch(targetUrl.toString(), {
      method: "GET",
      headers: requestHeaders,
      redirect: "manual"
    });

    const responseHeaders = cloneResponseHeaders(response);
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders
    });
  } catch (err) {
    return new Response(`OAuth Callback Proxy Error: ${err.message}`, { status: 500 });
  }
};
