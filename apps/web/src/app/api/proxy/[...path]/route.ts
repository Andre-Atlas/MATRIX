// Server-side proxy from the browser to the MATRIX API, injecting the server's MATRIX_API_KEY.
// MITIGATION ONLY until the G0 login (T24) lands: the web has no login yet, so whoever reaches this
// server gets the key's full read/write access. Hence the strict path, same-origin and size checks
// below, and the compose publishing the web on 127.0.0.1 only.

const MAX_PROXY_BODY_BYTES = 10 * 1024 * 1024;

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);
// Response headers passed back to the browser (never authorization, cookies or anything else).
const RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "x-content-type-options",
  "content-security-policy",
  "cache-control",
];
const BAD_SEGMENT = /[/\\%\u0000-\u001f\u007f]/;

type Ctx = { params: Promise<{ path: string[] }> };

function error(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: { "cache-control": "no-store" } });
}

function validSegment(seg: string): boolean {
  return seg !== "" && seg !== "." && seg !== ".." && !BAD_SEGMENT.test(seg);
}

/** Same-origin check for mutations: the Origin host must match the Host the request came to. */
function sameOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return req.headers.get("sec-fetch-site") === "same-origin";
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }
  const host = req.headers.get("host") ?? new URL(req.url).host;
  return originHost === host;
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

async function forward(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  const method = req.method.toUpperCase();
  if (!Array.isArray(path) || path.length === 0 || !path.every(validSegment)) {
    return error(400, "invalid path");
  }
  if (MUTATING.has(method) && !sameOrigin(req)) {
    return error(403, "cross-origin request refused");
  }
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
    return mockFetch(method, apiPath, params, hasBody ? await req.text() : null);
  }

  const base = (process.env.MATRIX_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
  const root = new URL(`${base}/api/v1/`);
  const url = new URL(`${base}${apiPath}`);
  if (url.origin !== root.origin || !url.pathname.startsWith(root.pathname)) {
    return error(400, "invalid path");
  }
  url.search = search;

  const headers: Record<string, string> = {};
  const ct = req.headers.get("content-type");
  if (ct) headers["content-type"] = ct;
  const key = process.env.MATRIX_API_KEY;
  if (key) headers.authorization = `Bearer ${key}`;

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
  return new Response(res.body, { status: res.status, headers: out });
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
