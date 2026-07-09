export default async (request, context) => {
  const url = new URL(request.url);
  if (url.pathname === "/" && (url.searchParams.has("code") || url.searchParams.has("error"))) {
    const callbackUrl = new URL("/oauth/callback", url.origin);
    url.searchParams.forEach((value, key) => {
      callbackUrl.searchParams.append(key, value);
    });
    callbackUrl.searchParams.set("landed_redirect_uri", `${url.origin}/`);
    return Response.redirect(callbackUrl.toString(), 302);
  }
  return context.next();
};
