import type { NextRequest } from "next/server";
import { cleanBaseUrl } from "@/lib/api";

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const cleanPath = path[0] === "api" && path[1] === "v1" ? path.slice(2) : path;
  const subPath = cleanPath.join("/");
  const apiPath = `/api/v1/${subPath}`;
  const hasBody = req.method !== "GET" && req.method !== "DELETE";

  if (subPath === "auth/logout" && req.method === "POST") {
    const logoutHeaders = new Headers({ "content-type": "application/json" });
    logoutHeaders.append("set-cookie", "matrix_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax");
    logoutHeaders.append("set-cookie", "matrix_project_id=; Path=/; Max-Age=0; SameSite=Lax");
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: logoutHeaders,
    });
  }

  // Development/screenshot fixtures; the module is only loaded when the flag is set.
  if (process.env.MATRIX_MOCK === "1" || process.env.NEXT_PUBLIC_MATRIX_MOCK === "1") {
    const { mockFetch } = await import("@/lib/mock-api");
    const params = Object.fromEntries(req.nextUrl.searchParams);
    return mockFetch(req.method, apiPath, params, hasBody ? await req.text() : null);
  }

  const base = cleanBaseUrl();
  const url = new URL(`${base}${apiPath}`);
  url.search = req.nextUrl.search;
  const headers: Record<string, string> = {};
  const ct = req.headers.get("content-type");
  if (ct) headers["content-type"] = ct;

  const cookie = req.cookies.get("matrix_session")?.value;
  const key = process.env.MATRIX_API_KEY;
  if (cookie) {
    headers.authorization = `Bearer ${cookie}`;
    if (key) headers["x-matrix-api-key"] = key;
  } else if (key) {
    headers.authorization = `Bearer ${key}`;
  }

  const proj = req.cookies.get("matrix_project_id")?.value;
  if (proj) headers["x-matrix-project-id"] = proj;

  const bodyText = hasBody ? await req.text() : undefined;

  let res: Response;
  try {
    res = await fetch(url, {
      method: req.method,
      headers,
      body: bodyText,
      cache: "no-store",
    });
  } catch (err) {
    if (process.env.MATRIX_FALLBACK_TO_MOCK === "1") {
      const { mockFetch } = await import("@/lib/mock-api");
      const params = Object.fromEntries(req.nextUrl.searchParams);
      return mockFetch(req.method, apiPath, params, bodyText ?? null);
    }
    return new Response(
      JSON.stringify({
        detail: `Failed to connect to MATRIX backend at ${url.origin}: ${(err as Error).message}. Check MATRIX_API_URL or run with MATRIX_MOCK=1`,
      }),
      { status: 502, headers: { "content-type": "application/json" } }
    );
  }

  if (res.status === 401 && cookie && key) {
    try {
      res = await fetch(url, {
        method: req.method,
        headers: {
          ...headers,
          authorization: `Bearer ${key}`,
        },
        body: bodyText,
        cache: "no-store",
      });
    } catch {
      // keep original 401 response if retry fails
    }
  }

  const respHeaders = new Headers();
  respHeaders.set("content-type", res.headers.get("content-type") ?? "application/json");
  const cd = res.headers.get("content-disposition");
  if (cd) respHeaders.set("content-disposition", cd);

  const setCookies =
    typeof res.headers.getSetCookie === "function"
      ? res.headers.getSetCookie()
      : res.headers.get("set-cookie")
        ? [res.headers.get("set-cookie")!]
        : [];
  for (const sc of setCookies) {
    respHeaders.append("set-cookie", sc);
  }

  return new Response(res.body, {
    status: res.status,
    headers: respHeaders,
  });
}

export {
  forward as GET,
  forward as POST,
  forward as PUT,
  forward as PATCH,
  forward as DELETE,
};
