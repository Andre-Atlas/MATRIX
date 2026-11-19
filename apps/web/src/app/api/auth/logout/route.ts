// Logout: revokes the session in the API (best effort) and ALWAYS clears the cookies.
import { callApi, noStore } from "@/lib/auth-calls";
import { sameOrigin } from "@/lib/same-origin";
import { clearSessionCookies, sessionFromCookieHeader } from "@/lib/session-cookie";

export async function POST(req: Request): Promise<Response> {
  if (!sameOrigin(req)) return noStore({ detail: "cross-origin request refused" }, 403);
  const session = sessionFromCookieHeader(req.headers.get("cookie"));
  let revoked = false;
  if (session) {
    try {
      const res = await callApi(req, "POST", "/api/v1/auth/logout", session);
      revoked = res.ok;
      await res.body?.cancel();
    } catch {
      /* API down: still sign out locally */
    }
  }
  const out = noStore({ ok: true, revoked });
  clearSessionCookies(out.headers);
  return out;
}
