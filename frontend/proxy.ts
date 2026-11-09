import { NextResponse, type NextRequest } from "next/server";

/** The admin UI (UI_ADMIN=true) is the panel only: the public pages live on the public site. Domain pages stay
 *  because the admin console links to them. The public site passes through (it answers 404 on /admin). */
export function proxy(request: NextRequest) {
  if (process.env.UI_ADMIN !== "true") return NextResponse.next();
  const { pathname } = request.nextUrl;
  if (pathname === "/admin" || pathname.startsWith("/admin/") || pathname.startsWith("/domain/")) {
    return NextResponse.next();
  }
  const panel = request.nextUrl.clone();
  panel.pathname = "/admin";
  panel.search = "";
  return NextResponse.redirect(panel);
}

export const config = { matcher: ["/((?!_next/|.*\\.[a-zA-Z0-9]+$).*)"] };
