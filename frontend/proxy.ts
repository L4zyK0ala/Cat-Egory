import { NextResponse, type NextRequest } from "next/server";

/** The browser may talk to this site and to the one API this container is configured for, nothing else. Built per
 *  request because the same image runs as the public site and as the admin UI, each with its own API address.
 *  Scripts: only those carrying this request's nonce (Next.js adds it to its own scripts on dynamically rendered
 *  pages, which every page here is) and what they load ('strict-dynamic'); no 'unsafe-inline'. Styles keep
 *  'unsafe-inline': the pages use style attributes, which a nonce cannot cover. */
function contentSecurityPolicy(nonce: string): string {
  let api = "";
  try {
    api = new URL(process.env.PUBLIC_API_URL || process.env.API_INTERNAL_URL || "").origin;
  } catch {
    // no API address configured: the browser may only talk to this site
  }
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self' 'unsafe-inline'",
    "object-src 'none'",
    "img-src 'self' blob: data:",
    `connect-src 'self' ${api}`.trim(),
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");
}

/** The admin UI (UI_ADMIN=true) is the panel only: the public pages live on the public site. Domain pages stay
 *  because the admin console links to them. The public site passes through (it answers 404 on /admin). */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  try {
    decodeURIComponent(pathname);
  } catch {
    // A broken %-escape (/domain/%zz) otherwise fails inside the router as a 500 with no page.
    return new NextResponse("Bad request\n", { status: 400, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
  if (process.env.UI_ADMIN === "true"
      && pathname !== "/admin" && !pathname.startsWith("/admin/") && !pathname.startsWith("/domain/")) {
    const panel = request.nextUrl.clone();
    panel.pathname = "/admin";
    panel.search = "";
    return NextResponse.redirect(panel);
  }
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = contentSecurityPolicy(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);  // Next.js reads the nonce from here while rendering
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

// Named files only: a pattern for "has an extension" would also skip /domain/example.com.
export const config = { matcher: ["/((?!_next/|icon\\.svg|logo\\.svg).*)"] };
