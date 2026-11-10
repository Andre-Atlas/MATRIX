// Server-side proxy from the browser to the MATRIX API. The browser never holds a credential: the
// session token lives in the HttpOnly `matrix_session` cookie and is turned into
// `Authorization: Bearer` + `X-Matrix-Project` here (ADR-006). No cookie -> 401 without calling the
// API. Hardening kept from the pre-login version: strict path segments, same-origin check on
// mutations, streamed body limit, an allowlist of response headers. The login and logout routes of
// the API are not reachable through here (the login response carries the token).
import { sameOrigin } from "@/lib/same-origin";
import { clearSessionCookies, sessionFromCookieHeader } from "@/lib/session-cookie";
import { apiBase, upstreamHeaders } from "@/lib/upstream";

const MAX_PROXY_BODY_BYTES = 10 * 1024 * 1024;

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);
// Response headers passed back to the browser (never authorization, cookies or anything else).
const RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "x-content-type-options",
  "content-security-policy",
  "cache-control",
  "retry-after",
];
const BAD_SEGMENT = /[/\\%\u0000-\u001f\u007f]/;

type Ctx = { params: Promise<{ path: string[] }> };

function error(status: number, detail: string, extra?: (h: Headers) => void): Response {
  const res = Response.json({ detail }, { status, headers: { "cache-control": "no-store" } });
  extra?.(res.headers);
  return res;
}

/** Paths whose responses carry a session token: only the dedicated /api/auth/* routes may call them. */
function blocked(path: string[]): boolean {
  return path[0] === "auth" && (path[1] === "login" || path[1] === "logout");
}

function validSegment(seg: string): boolean {
  return seg !== "" && seg !== "." && seg !== ".." && !BAD_SEGMENT.test(seg);
}

class BodyTooLarge extends Error {}

/** The request body as a stream that errors once more than `limit` bytes went through. */
function limitedBody(body: ReadableStream<Uint8Array>, limit: number, onOverflow: () => void) {
  let seen = 0;
  return body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        seen += chunk.byteLength;
        if (seen > limit) {
          onOverflow();
          controller.error(new BodyTooLarge());
          return;
        }
        controller.enqueue(chunk);
      },
    }),
  );
}

/** The API says the session is gone: drop the cookies so the next navigation goes to /login. */
function expired(res: Response): Response {
  clearSessionCookies(res.headers);
  res.headers.set("cache-control", "no-store");
  return res;
}

async function forward(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  const method = req.method.toUpperCase();
  if (!Array.isArray(path) || path.length === 0 || !path.every(validSegment)) {
    return error(400, "invalid path");
  }
  if (blocked(path)) return error(404, "not found");
  if (MUTATING.has(method) && !sameOrigin(req)) {
    return error(403, "cross-origin request refused");
  }
  const session = sessionFromCookieHeader(req.headers.get("cookie"));
  if (!session) return error(401, "not signed in");
  const declared = req.headers.get("content-length");
  if (declared !== null && Number(declared) > MAX_PROXY_BODY_BYTES) {
    return error(413, "request body too large");
  }
  const hasBody = method !== "GET" && method !== "HEAD" && method !== "DELETE";
  const search = new URL(req.url).search;
  const apiPath = `/api/v1/${path.map(encodeURIComponent).join("/")}`;

  // Development/screenshot fixtures; the module is only loaded when the flag is set.
  if (process.env.MATRIX_MOCK === "1") {
    const { mockFetch } = await import("@/lib/mock-api");
    const params = Object.fromEntries(new URL(req.url).searchParams);
    const res = await mockFetch(method, apiPath, params, hasBody ? await req.text() : null, {
      token: session.token,
      projectId: session.projectId,
    });
    return res.status === 401 ? expired(res) : res;
  }

  const base = apiBase();
  const root = new URL(`${base}/api/v1/`);
  const url = new URL(`${base}${apiPath}`);
  if (url.origin !== root.origin || !url.pathname.startsWith(root.pathname)) {
    return error(400, "invalid path");
  }
  url.search = search;

  // Built from scratch: nothing the browser sent (authorization, project, client-IP headers) is
  // forwarded. The trusted-proxy header, if configured, is overwritten with the IP Next sees.
  const ct = req.headers.get("content-type");
  const noProject = path[0] === "auth" || (path.length === 1 && path[0] === "projects");
  const headers = upstreamHeaders(req, noProject ? { ...session, projectId: null } : session, ct ? { "content-type": ct } : {});

  let overflow = false;
  const body =
    hasBody && req.body ? limitedBody(req.body, MAX_PROXY_BODY_BYTES, () => (overflow = true)) : undefined;
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers,
      body,
      cache: "no-store",
      ...(body ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch {
    if (overflow) return error(413, "request body too large");
    return error(502, "MATRIX API unreachable");
  }
  if (overflow) {
    await res.body?.cancel();
    return error(413, "request body too large");
  }

  const out = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = res.headers.get(name);
    if (value !== null) out.set(name, value);
  }
  if (!out.has("content-type")) out.set("content-type", "application/json");
  const response = new Response(res.body, { status: res.status, headers: out });
  return res.status === 401 ? expired(response) : response;
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
