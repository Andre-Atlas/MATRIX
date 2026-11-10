// Switch the active project: only to one the API says the user belongs to.
import { callApi, noStore } from "@/lib/auth-calls";
import { sameOrigin } from "@/lib/same-origin";
import {
  PROJECT_COOKIE,
  PROJECT_ID_RE,
  clearSessionCookies,
  serializeCookie,
  sessionFromCookieHeader,
} from "@/lib/session-cookie";
import type { MeResponse } from "@/lib/types";

export async function POST(req: Request): Promise<Response> {
  if (!sameOrigin(req)) return noStore({ detail: "cross-origin request refused" }, 403);
  const session = sessionFromCookieHeader(req.headers.get("cookie"));
  if (!session) return noStore({ detail: "not signed in" }, 401);
  let id: unknown;
  try {
    id = ((await req.json()) as { project_id?: unknown }).project_id;
  } catch {
    return noStore({ detail: "Invalid request." }, 400);
  }
  if (typeof id !== "string" || !PROJECT_ID_RE.test(id)) return noStore({ detail: "Invalid project." }, 400);

  let res: Response;
  try {
    res = await callApi(req, "GET", "/api/v1/auth/me", session);
  } catch {
    return noStore({ detail: "Could not reach the MATRIX API." }, 502);
  }
  if (res.status === 401) {
    const out = noStore({ detail: "Your session has expired." }, 401);
    clearSessionCookies(out.headers);
    return out;
  }
  if (!res.ok) {
    await res.body?.cancel();
    return noStore({ detail: "Could not switch project." }, 502);
  }
  const me = (await res.json()) as MeResponse;
  const target = me.memberships.find((m) => m.project.id === id);
  if (!target) return noStore({ detail: "You are not a member of that project." }, 404);

  // Same lifetime as the session cookie: the remaining one is unknown here, so reuse a long
  // default bounded by the API's own absolute session limit (30 d). The token cookie is untouched.
  const out = noStore({ ok: true, project: target.project, role: target.role });
  out.headers.append("set-cookie", serializeCookie(PROJECT_COOKIE, id, 30 * 86_400));
  return out;
}
