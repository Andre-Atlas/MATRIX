// Calls from the /api/auth/* route handlers to the MATRIX API (or the mock).
import { apiBase, clientIp, upstreamHeaders } from "./upstream";
import type { Session } from "./session-cookie";

export const AUTH_TIMEOUT_MS = 10_000;

export async function callApi(
  req: Request,
  method: "GET" | "POST",
  path: string,
  session: Session | null,
  body?: unknown,
): Promise<Response> {
  const json = body === undefined ? undefined : JSON.stringify(body);
  if (process.env.MATRIX_MOCK === "1") {
    const { mockFetch } = await import("./mock-api");
    return mockFetch(method, path, {}, json ?? null, {
      token: session?.token ?? null,
      projectId: null,
      ip: clientIp(req),
      userAgent: req.headers.get("user-agent"),
    });
  }
  // The user agent is forwarded so the sessions list can tell devices apart; the client IP goes in
  // the trusted-proxy header (overwritten, never copied from the browser).
  const ua = req.headers.get("user-agent");
  return fetch(`${apiBase()}${path}`, {
    method,
    headers: upstreamHeaders(req, session ? { ...session, projectId: null } : null, {
      ...(json !== undefined ? { "content-type": "application/json" } : {}),
      ...(ua ? { "user-agent": ua.slice(0, 300) } : {}),
    }),
    body: json,
    cache: "no-store",
    signal: AbortSignal.timeout(AUTH_TIMEOUT_MS),
  });
}

export function retryAfterSeconds(res: Response): number | null {
  const n = Number(res.headers.get("retry-after"));
  return Number.isFinite(n) && n > 0 ? Math.min(3600, Math.ceil(n)) : null;
}

export function noStore(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}
