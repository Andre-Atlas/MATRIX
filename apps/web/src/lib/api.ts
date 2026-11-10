// Server-side API client (Server Components and route handlers). The session token is read from
// the HttpOnly cookie here and sent as `Authorization: Bearer` + `X-Matrix-Project`; it never
// reaches client components. MATRIX_API_KEY is no longer used (the mock needs no key either).
import { headers as nextHeaders } from "next/headers";
import { redirect } from "next/navigation";
import { ApiError } from "./api-error";
import { sessionFromCookieHeader, type Session } from "./session-cookie";
import { apiBase, upstreamHeaders } from "./upstream";

export { ApiError, isNotFound } from "./api-error";

const base = apiBase;

/** Routes that work without a project (`/auth/*`, `/projects`): the project header is not sent. */
const NO_PROJECT = /^\/api\/v1\/(auth\/|projects$)/;

export async function currentSession(): Promise<Session | null> {
  return sessionFromCookieHeader((await nextHeaders()).get("cookie"));
}

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const ra = Number(res.headers.get("retry-after"));
    throw new ApiError(res.status, await res.text(), Number.isFinite(ra) && ra > 0 ? ra : undefined);
  }
  if (res.status === 204) return null as T;
  return (await res.json()) as T;
}

/** An expired/invalid session goes through /api/auth/expired, which clears the cookies (a Server
 * Component cannot) and lands on /login?next=… . A stale project (no longer a member) re-picks. */
async function bounce(err: unknown, path: string): Promise<never> {
  if (err instanceof ApiError) {
    if (err.status === 401) {
      const here = (await nextHeaders()).get("x-matrix-path") ?? "/";
      redirect(`/api/auth/expired?next=${encodeURIComponent(here)}`);
    }
    if (err.status === 404 && /"detail"\s*:\s*"project not found"/.test(err.body) && !NO_PROJECT.test(path)) {
      redirect("/projects?stale=1");
    }
  }
  throw err;
}

async function sessionFor(path: string): Promise<Session | null> {
  const session = await currentSession();
  if (!session) redirect("/login");
  if (!NO_PROJECT.test(path) && !session.projectId) redirect("/projects");
  return session;
}

async function mockLatency() {
  const ms = Number(process.env.MATRIX_MOCK_LATENCY_MS ?? 0);
  if (ms > 0) await new Promise((r) => setTimeout(r, Math.min(ms, 5000)));
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
): Promise<T> {
  const session = await sessionFor(path);
  try {
    // Development/screenshot fixtures; the module is only loaded when the flag is set.
    if (process.env.MATRIX_MOCK === "1") {
      const { mockGet } = await import("./mock-api");
      await mockLatency();
      return await mockGet<T>(path, params, { token: session!.token, projectId: NO_PROJECT.test(path) ? null : session!.projectId });
    }
    const url = new URL(`${base()}${path}`);
    for (const [k, v] of Object.entries(params ?? {})) {
      if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
    }
    const res = await fetch(url, {
      headers: upstreamHeaders(null, NO_PROJECT.test(path) ? { ...session!, projectId: null } : session),
      cache: "no-store",
    });
    return await parse<T>(res);
  } catch (e) {
    return bounce(e, path);
  }
}

export async function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  const session = await sessionFor(path);
  try {
    if (process.env.MATRIX_MOCK === "1") {
      const { mockSend, NO_CONTENT } = await import("./mock-api");
      const out = await mockSend<T>(method, path, body, { token: session!.token, projectId: NO_PROJECT.test(path) ? null : session!.projectId });
      return (out === (NO_CONTENT as unknown) ? null : out) as T;
    }
    const res = await fetch(`${base()}${path}`, {
      method,
      headers: upstreamHeaders(
        null,
        NO_PROJECT.test(path) ? { ...session!, projectId: null } : session,
        body === undefined ? {} : { "content-type": "application/json" },
      ),
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    return await parse<T>(res);
  } catch (e) {
    return bounce(e, path);
  }
}
