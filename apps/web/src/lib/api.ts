import { cookies } from "next/headers";
import { ApiError } from "./api-error";

export { ApiError, isNotFound } from "./api-error";

export function cleanBaseUrl(raw?: string): string {
  let b = (raw ?? process.env.MATRIX_API_URL ?? "http://localhost:8000").trim();
  b = b.replace(/\/+$/, "");
  b = b.replace(/\/api\/v1$/, "").replace(/\/api$/, "");
  return b;
}

export function buildApiUrl(path: string, params?: Record<string, string | number | undefined>): URL {
  const b = cleanBaseUrl();
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  const url = new URL(`${b}${cleanPath}`);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  }
  return url;
}

const base = () => cleanBaseUrl();

async function headers(json: boolean): Promise<Record<string, string>> {
  const h: Record<string, string> = {};
  let sessionToken: string | undefined;
  let projectId: string | undefined;
  try {
    const c = await cookies();
    sessionToken = c.get("matrix_session")?.value;
    projectId = c.get("matrix_project_id")?.value;
  } catch {
    // cookies() unavailable outside request context (e.g. build / tests)
  }
  if (sessionToken) {
    h.Authorization = `Bearer ${sessionToken}`;
  } else {
    const key = process.env.MATRIX_API_KEY;
    if (key) h.Authorization = `Bearer ${key}`;
  }
  if (projectId) {
    h["x-matrix-project-id"] = projectId;
  }
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function parse<T>(res: Response, reqUrl?: string): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    let hint = "";
    if (res.status === 404) {
      hint = ` (endpoint not found on backend${reqUrl ? ` at ${reqUrl}` : ""}; check MATRIX_API_URL or run with MATRIX_MOCK=1)`;
    } else if (res.status === 401) {
      hint = ` (unauthorized; check MATRIX_API_KEY in .env.local)`;
    }
    throw new ApiError(res.status, `${body}${hint}`);
  }
  return (await res.json()) as T;
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
): Promise<T> {
  // Development/screenshot fixtures; the module is only loaded when the flag is set.
  if (process.env.MATRIX_MOCK === "1" || process.env.NEXT_PUBLIC_MATRIX_MOCK === "1") {
    const { mockGet } = await import("./mock-api");
    return mockGet<T>(path, params);
  }
  const url = buildApiUrl(path, params);
  try {
    const res = await fetch(url, { headers: await headers(false), cache: "no-store" });
    return await parse<T>(res, url.toString());
  } catch (err) {
    if (process.env.MATRIX_FALLBACK_TO_MOCK === "1") {
      console.warn(`[MATRIX] Backend call to ${url} failed. Falling back to mock data (MATRIX_FALLBACK_TO_MOCK=1).`);
      const { mockGet } = await import("./mock-api");
      return mockGet<T>(path, params);
    }
    throw err;
  }
}

export async function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (process.env.MATRIX_MOCK === "1" || process.env.NEXT_PUBLIC_MATRIX_MOCK === "1") {
    const { mockSend } = await import("./mock-api");
    return mockSend<T>(method, path, body);
  }
  const url = buildApiUrl(path);
  try {
    const res = await fetch(url, {
      method,
      headers: await headers(true),
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    return await parse<T>(res, url.toString());
  } catch (err) {
    if (process.env.MATRIX_FALLBACK_TO_MOCK === "1") {
      console.warn(`[MATRIX] Backend call to ${url} failed. Falling back to mock data (MATRIX_FALLBACK_TO_MOCK=1).`);
      const { mockSend } = await import("./mock-api");
      return mockSend<T>(method, path, body);
    }
    throw err;
  }
}

