// Landing point for a Server Component that got a 401 (it cannot clear cookies itself). It asks the
// API again so a stray link cannot sign anybody out: only a really dead session is cleared.
import { callApi } from "@/lib/auth-calls";
import { clearSessionCookies, safeNext, sessionFromCookieHeader } from "@/lib/session-cookie";

export async function GET(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const next = safeNext(url.searchParams.get("next"));
  // Relative Location: behind a reverse proxy the origin Next sees may be an internal one.
  const toLogin = `/login?next=${encodeURIComponent(next)}`;
  const session = sessionFromCookieHeader(req.headers.get("cookie"));
  let dead = true;
  if (session) {
    try {
      const res = await callApi(req, "GET", "/api/v1/auth/me", session);
      await res.body?.cancel();
      dead = res.status === 401;
    } catch {
      dead = false;
    }
  }
  if (!dead) return new Response(null, { status: 303, headers: { location: next, "cache-control": "no-store" } });
  const res = new Response(null, { status: 303, headers: { location: toLogin, "cache-control": "no-store" } });
  clearSessionCookies(res.headers);
  return res;
}
