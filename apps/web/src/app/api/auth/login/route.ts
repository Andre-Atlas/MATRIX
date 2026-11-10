// Login: talks to the API, then stores the token in HttpOnly cookies. The response to the browser
// never contains the token. Failures are reported without leaking backend details.
import { callApi, noStore, retryAfterSeconds } from "@/lib/auth-calls";
import { sameOrigin } from "@/lib/same-origin";
import {
  PROJECT_COOKIE,
  PROJECT_ID_RE,
  TOKEN_RE,
  lifetimeSeconds,
  parseCookies,
  safeNext,
  setSessionCookies,
} from "@/lib/session-cookie";
import { roleRank, type Membership } from "@/lib/types";

const MAX_BODY = 8 * 1024;

export async function POST(req: Request): Promise<Response> {
  if (!sameOrigin(req)) return noStore({ detail: "cross-origin request refused" }, 403);
  const declared = Number(req.headers.get("content-length") ?? 0);
  if (declared > MAX_BODY) return noStore({ detail: "request body too large" }, 413);
  let input: { email?: unknown; password?: unknown; next?: unknown };
  try {
    const text = await req.text();
    if (text.length > MAX_BODY) return noStore({ detail: "request body too large" }, 413);
    input = JSON.parse(text);
  } catch {
    return noStore({ detail: "Invalid request." }, 400);
  }
  if (typeof input.email !== "string" || typeof input.password !== "string" || !input.email || !input.password) {
    return noStore({ detail: "Enter your email and password." }, 400);
  }

  let res: Response;
  try {
    res = await callApi(req, "POST", "/api/v1/auth/login", null, { email: input.email, password: input.password });
  } catch {
    return noStore({ detail: "Could not reach the MATRIX API. Try again shortly." }, 502);
  }

  if (res.status === 401 || res.status === 400 || res.status === 422) {
    await res.body?.cancel();
    return noStore({ detail: "Invalid email or password." }, 401);
  }
  if (res.status === 429 || res.status === 503) {
    const retry = retryAfterSeconds(res) ?? (res.status === 429 ? 60 : 5);
    await res.body?.cancel();
    return noStore(
      {
        detail: res.status === 429 ? "Too many attempts." : "The server is busy.",
        retry_after: retry,
      },
      res.status,
    );
  }
  if (!res.ok) {
    await res.body?.cancel();
    return noStore({ detail: "Sign in failed. Try again shortly." }, 502);
  }

  let data: { token?: unknown; expires_at?: unknown; user?: unknown; memberships?: unknown };
  try {
    data = await res.json();
  } catch {
    return noStore({ detail: "Sign in failed. Try again shortly." }, 502);
  }
  const maxAge = lifetimeSeconds(data.expires_at);
  if (typeof data.token !== "string" || !TOKEN_RE.test(data.token) || maxAge === null || !Array.isArray(data.memberships)) {
    return noStore({ detail: "Sign in failed. Try again shortly." }, 502);
  }
  const memberships = data.memberships as Membership[];
  // Keep the previously selected project when the user still belongs to it.
  const previous = parseCookies(req.headers.get("cookie"))[PROJECT_COOKIE];
  const usable = memberships.filter((m) => PROJECT_ID_RE.test(m.project.id));
  const pick =
    usable.find((m) => m.project.id === previous) ??
    [...usable].sort((a, b) => roleRank(b.role) - roleRank(a.role))[0];

  const out = noStore({
    ok: true,
    next: safeNext(input.next),
    user: data.user,
    memberships,
    project_id: pick?.project.id ?? null,
  });
  setSessionCookies(out.headers, data.token, pick?.project.id ?? null, maxAge);
  return out;
}
