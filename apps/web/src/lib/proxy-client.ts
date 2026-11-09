// Browser-side calls to the API go through /api/proxy/<path> (the key is added server-side).

export class ProxyError extends Error {
  constructor(
    public readonly status: number,
    message: string,
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

export async function proxy<T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
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
  if (!res.ok) throw new ProxyError(res.status, errorMessage(res.status, text));
  return (text ? JSON.parse(text) : null) as T;
}
