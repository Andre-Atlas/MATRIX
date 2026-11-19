// Current user and memberships (never the token).
import { callApi, noStore } from "@/lib/auth-calls";
import { clearSessionCookies, sessionFromCookieHeader } from "@/lib/session-cookie";

export async function GET(req: Request): Promise<Response> {
  const session = sessionFromCookieHeader(req.headers.get("cookie"));
  if (!session) return noStore({ detail: "not signed in" }, 401);
  let res: Response;
  try {
    res = await callApi(req, "GET", "/api/v1/auth/me", session);
  } catch {
    return noStore({ detail: "Could not reach the MATRIX API." }, 502);
  }
  if (res.status === 401) {
    await res.body?.cancel();
    const out = noStore({ detail: "Your session has expired." }, 401);
    clearSessionCookies(out.headers);
    return out;
  }
  if (!res.ok) {
    await res.body?.cancel();
    return noStore({ detail: "Could not load your profile." }, 502);
  }
  return noStore({ ...(await res.json()), project_id: session.projectId });
}
