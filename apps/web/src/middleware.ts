// Gate: no session cookie -> /login?next=… (pages) or 401 (API). It only checks that a well-formed
// token cookie exists; the API decides whether it is still valid (a 401 there clears the cookie and
// comes back to /login through /api/auth/expired). It also tells Server Components the current path.
import { NextResponse, type NextRequest } from "next/server";
import { clearSessionCookies, safeNext, sessionCookieConflict, sessionFromCookieHeader } from "@/lib/session-cookie";

// Reachable without a session. /api/auth/expired decides for itself (it clears and redirects).
const PUBLIC = new Set(["/login", "/api/auth/login", "/api/auth/expired"]);

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const here = `${pathname}${search}`;
  const forward = new Headers(req.headers);
  // Never trust a client-supplied value for the internal path header.
  forward.set("x-matrix-path", safeNext(here));

  const cookie = req.headers.get("cookie");
  // Two cookies with the session/project name: one was planted. Refuse the session and clear both.
  const conflict = sessionCookieConflict(cookie);
  if (!conflict && (PUBLIC.has(pathname) || sessionFromCookieHeader(cookie))) {
    return NextResponse.next({ request: { headers: forward } });
  }
  if (conflict && pathname === "/login") {
    const res = NextResponse.next({ request: { headers: forward } });
    clearSessionCookies(res.headers);
    return res;
  }
  if (pathname.startsWith("/api/")) {
    const res = NextResponse.json({ detail: "not signed in" }, { status: 401, headers: { "cache-control": "no-store" } });
    if (conflict) clearSessionCookies(res.headers);
    return res;
  }
  const login = req.nextUrl.clone();
  login.pathname = "/login";
  login.search = "";
  const next = safeNext(here);
  if (next !== "/") login.searchParams.set("next", next);
  const res = NextResponse.redirect(login);
  if (conflict) clearSessionCookies(res.headers);
  return res;
}

export const config = {
  // Everything except Next's static assets and the app icon.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
