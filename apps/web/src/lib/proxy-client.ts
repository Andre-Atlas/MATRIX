// Browser-side calls to the API go through /api/proxy/<path>: the session cookie is HttpOnly, so
// the credential is added server-side and this code never sees a token.

export class ProxyError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    /** Seconds from Retry-After (429/503). */
    public readonly retryAfter?: number,
  ) {
    super(message);
    this.name = "ProxyError";
  }
}

/** Human-readable message from an API error body (FastAPI `detail` string or validation list). */
export function errorMessage(status: number, body: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    const d = parsed.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d))
      return d
        .map((e: { loc?: unknown[]; msg?: string }) => [e.loc?.slice(1).join("."), e.msg].filter(Boolean).join(": "))
        .join("; ");
  } catch {
    /* not JSON */
  }
  return body.trim() ? `${status}: ${body.slice(0, 200)}` : `Request failed (${status})`;
}

/** Text for an error shown next to a form: the API's message with the status made explicit. */
export function explain(e: unknown): string {
  if (!(e instanceof ProxyError)) return e instanceof Error ? e.message : "Something went wrong.";
  const m = e.message.charAt(0).toUpperCase() + e.message.slice(1);
  const wait = e.retryAfter ? ` Try again in ${e.retryAfter} s.` : "";
  if (e.status === 403) return `Not allowed: ${m.replace(/\.$/, "")}.`;
  if (e.status === 404) return `Not found: ${m.replace(/\.$/, "")}.`;
  if (e.status === 429) return `Too many attempts.${wait}`;
  if (e.status === 503) return `The server is busy.${wait}`;
  return m.endsWith(".") ? m : `${m}.`;
}

/** The session is gone: let the server clear the cookies and show the login page. */
function sessionExpired() {
  if (typeof window === "undefined") return;
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/api/auth/expired?next=${encodeURIComponent(here)}`);
}

export async function proxy<T>(method: "GET" | "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/proxy/${path.replace(/^\/+/, "")}`, {
      method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ProxyError(0, "Network error: the MATRIX web server did not answer.");
  }
  const text = await res.text();
  if (res.status === 401) sessionExpired();
  if (!res.ok) {
    const ra = Number(res.headers.get("retry-after"));
    throw new ProxyError(res.status, errorMessage(res.status, text), Number.isFinite(ra) && ra > 0 ? ra : undefined);
  }
  return (text ? JSON.parse(text) : null) as T;
}
