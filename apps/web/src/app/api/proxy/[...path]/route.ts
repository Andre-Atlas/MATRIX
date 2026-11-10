import type { NextRequest } from "next/server";

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const hasBody = req.method !== "GET" && req.method !== "DELETE";
  // Development/screenshot fixtures; the module is only loaded when the flag is set.
  if (process.env.MATRIX_MOCK === "1") {
    const { mockFetch } = await import("@/lib/mock-api");
    const params = Object.fromEntries(req.nextUrl.searchParams);
    return mockFetch(req.method, `/api/v1/${path.join("/")}`, params, hasBody ? await req.text() : null);
  }
  const base = process.env.MATRIX_API_URL ?? "http://localhost:8000";
  const url = new URL(`${base}/api/v1/${path.join("/")}`);
  url.search = req.nextUrl.search;
  const headers: Record<string, string> = {};
  const ct = req.headers.get("content-type");
  if (ct) headers["content-type"] = ct;
  const cookie = req.cookies.get("matrix_session")?.value;
  if (cookie) {
    headers.authorization = `Bearer ${cookie}`;
  } else {
    const key = process.env.MATRIX_API_KEY;
    if (key) headers.authorization = `Bearer ${key}`;
  }
  const proj = req.cookies.get("matrix_project_id")?.value;
  if (proj) headers["x-matrix-project-id"] = proj;

  const res = await fetch(url, {
    method: req.method,
    headers,
    body: hasBody ? await req.text() : undefined,
    cache: "no-store",
  });
  const respHeaders: Record<string, string> = {
    "content-type": res.headers.get("content-type") ?? "application/json",
    ...(res.headers.get("content-disposition")
      ? { "content-disposition": res.headers.get("content-disposition")! }
      : {}),
  };
  const setCookie = res.headers.get("set-cookie");
  if (setCookie) respHeaders["set-cookie"] = setCookie;

  return new Response(res.body, {
    status: res.status,
    headers: respHeaders,
  });
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE };
