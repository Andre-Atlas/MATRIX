import { cookies } from "next/headers";
import { ApiError } from "./api-error";

export { ApiError, isNotFound } from "./api-error";

const base = () => process.env.MATRIX_API_URL ?? "http://localhost:8000";

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
  const res = await fetch(url, { headers: await headers(false), cache: "no-store" });
  return parse<T>(res);
}

export async function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (process.env.MATRIX_MOCK === "1") {
    const { mockSend } = await import("./mock-api");
    return mockSend<T>(method, path, body);
  }
  const res = await fetch(`${base()}${path}`, {
    method,
    headers: await headers(true),
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  return parse<T>(res);
}
