import { ApiError } from "./api-error";

export { ApiError, isNotFound } from "./api-error";

const base = () => process.env.MATRIX_API_URL ?? "http://localhost:8000";

function headers(json: boolean): Record<string, string> {
  const h: Record<string, string> = {};
  const key = process.env.MATRIX_API_KEY;
  if (key) h.Authorization = `Bearer ${key}`;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return (await res.json()) as T;
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
): Promise<T> {
  // Development/screenshot fixtures; the module is only loaded when the flag is set.
  if (process.env.MATRIX_MOCK === "1") {
    const { mockGet } = await import("./mock-api");
    return mockGet<T>(path, params);
  }
  const url = new URL(`${base()}${path}`);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  }
  const res = await fetch(url, { headers: headers(false), cache: "no-store" });
  return parse<T>(res);
}

export async function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${base()}${path}`, {
    method,
    headers: headers(true),
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  return parse<T>(res);
}
