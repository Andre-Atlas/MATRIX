// Gate: no session cookie -> /login?next=… (pages) or 401 (API). It only checks that a well-formed
// token cookie exists; the API decides whether it is still valid (a 401 there clears the cookie and
// comes back to /login through /api/auth/expired). It also tells Server Components the current path.
import { NextResponse, type NextRequest } from "next/server";
import { safeNext, sessionFromCookieHeader } from "@/lib/session-cookie";

// Reachable without a session. /api/auth/expired decides for itself (it clears and redirects).
const PUBLIC = new Set(["/login", "/api/auth/login", "/api/auth/expired"]);

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const here = `${pathname}${search}`;
  const forward = new Headers(req.headers);
  // Never trust a client-supplied value for the internal path header.
  forward.set("x-matrix-path", safeNext(here));

  if (PUBLIC.has(pathname) || sessionFromCookieHeader(req.headers.get("cookie"))) {
    return NextResponse.next({ request: { headers: forward } });
  }
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "not signed in" }, { status: 401, headers: { "cache-control": "no-store" } });
  }
  const login = req.nextUrl.clone();
  login.pathname = "/login";
  login.search = "";
  const next = safeNext(here);
  if (next !== "/") login.searchParams.set("next", next);
  return NextResponse.redirect(login);
}

export const config = {
  // Everything except Next's static assets and the app icon.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
