// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DELETE, GET, POST } from "./route";

const TOKEN = "mxs_session_token_for_tests_0123456789";
const PROJECT = "a1b2c3d4e5f60718293a4b5c6d7e8f90";
const COOKIE = `matrix_session=${TOKEN}; matrix_project=${PROJECT}`;
const HOST = "matrix.local:3000";

function ctx(...path: string[]) {
  return { params: Promise.resolve({ path }) };
}

function req(method: string, headers: Record<string, string> = {}, body?: BodyInit) {
  return new Request(`http://${HOST}/api/proxy/x`, {
    method,
    headers: { host: HOST, cookie: COOKIE, ...headers },
    body,
    ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
  } as RequestInit);
}

const sameOrigin = { origin: `http://${HOST}` };

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("MATRIX_API_URL", "http://api:8000");
  vi.stubEnv("MATRIX_API_KEY", "mx_global_key_must_not_be_used");
  vi.stubEnv("MATRIX_TRUSTED_PROXY_HEADER", "");
  vi.stubEnv("MATRIX_MOCK", "");
  fetchMock = vi.fn(async (_url: URL, init?: RequestInit) => {
    // Consume the body like a real upstream would.
    if (init?.body) await new Response(init.body).arrayBuffer();
    return new Response('{"ok":true}', {
      status: 200,
      headers: {
        "content-type": "application/json",
        "content-disposition": 'attachment; filename="a.bin"',
        "x-content-type-options": "nosniff",
        "content-security-policy": "sandbox",
        "cache-control": "private, max-age=60",
        authorization: `Bearer ${TOKEN}`,
        "set-cookie": "s=1",
        "x-internal": "1",
      },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("proxy path validation", () => {
  it.each([
    [[".."]],
    [["traces", "..", "..", "health"]],
    [["."]],
    [[""]],
    [["a/b"]],
    [["a\\b"]],
    [["%2e%2e"]],
    [["%2E%2E", "health"]],
    [["a%2fb"]],
    [["a\u0000b"]],
    [["a\nb"]],
    [[]],
  ])("rejects %j with 400 without calling the API", async (path) => {
    const res = await GET(req("GET"), ctx(...path));
    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a valid path under /api/v1 with the query string", async () => {
    const r = new Request(`http://${HOST}/api/proxy/traces?limit=5`, { headers: { host: HOST, cookie: COOKIE } });
    const res = await GET(r, ctx("traces", "abc 1"));
    expect(res.status).toBe(200);
    const url = fetchMock.mock.calls[0][0] as URL;
    expect(url.toString()).toBe("http://api:8000/api/v1/traces/abc%201?limit=5");
  });
});

describe("same-origin check on mutations", () => {
  it("refuses a POST from another origin", async () => {
    const res = await POST(req("POST", { origin: "http://evil.example" }, "{}"), ctx("datasets"));
    expect(res.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a DELETE without Origin or same-origin fetch metadata", async () => {
    expect((await DELETE(req("DELETE"), ctx("datasets", "d1"))).status).toBe(403);
    expect((await DELETE(req("DELETE", { "sec-fetch-site": "cross-site" }), ctx("datasets", "d1"))).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts same-origin Origin or Sec-Fetch-Site", async () => {
    expect((await POST(req("POST", sameOrigin, "{}"), ctx("datasets"))).status).toBe(200);
    expect((await DELETE(req("DELETE", { "sec-fetch-site": "same-origin" }), ctx("datasets", "d1"))).status).toBe(200);
  });

  it("does not require Origin on GET", async () => {
    expect((await GET(req("GET"), ctx("traces"))).status).toBe(200);
  });
});

describe("request body", () => {
  it("rejects a declared Content-Length above 10 MiB with 413", async () => {
    const r = req("POST", { ...sameOrigin, "content-length": String(10 * 1024 * 1024 + 1) });
    const res = await POST(r, ctx("datasets"));
    expect(res.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a streamed body above 10 MiB with 413", async () => {
    const chunk = new Uint8Array(1024 * 1024);
    let sent = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent++ < 12) controller.enqueue(chunk);
        else controller.close();
      },
    });
    const res = await POST(req("POST", sameOrigin, stream), ctx("datasets", "d1", "items"));
    expect(res.status).toBe(413);
  });

  it("forwards binary bodies unchanged", async () => {
    const bytes = new Uint8Array([0, 255, 128, 10, 13, 0xc3, 0x28]);
    let received: Uint8Array | null = null;
    fetchMock.mockImplementationOnce(async (_url: URL, init?: RequestInit) => {
      received = new Uint8Array(await new Response(init!.body).arrayBuffer());
      return new Response(null, { status: 204 });
    });
    const res = await POST(
      req("POST", { ...sameOrigin, "content-type": "application/octet-stream" }, bytes),
      ctx("artifacts", "a1", "content"),
    );
    expect(res.status).toBe(204);
    expect(Array.from(received!)).toEqual(Array.from(bytes));
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["content-type"]).toBe("application/octet-stream");
  });
});

describe("session", () => {
  const noCookie = (method: string, headers: Record<string, string> = {}) =>
    new Request(`http://${HOST}/api/proxy/x`, { method, headers: { host: HOST, ...headers } });

  it("answers 401 without calling the API when there is no session cookie", async () => {
    const res = await GET(noCookie("GET"), ctx("traces"));
    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
    const post = await POST(noCookie("POST", sameOrigin), ctx("datasets"));
    expect(post.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("ignores a malformed token cookie and the global API key", async () => {
    const r = noCookie("GET", { cookie: "matrix_session=not-a-token; matrix_project=p1" });
    expect((await GET(r, ctx("traces"))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("turns the cookie into Authorization and X-Matrix-Project, never the global key", async () => {
    await GET(req("GET", { authorization: "Bearer client-supplied", "x-matrix-project": "evil" }), ctx("traces"));
    const h = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(h.authorization).toBe(`Bearer ${TOKEN}`);
    expect(h["x-matrix-project"]).toBe(PROJECT);
    expect(JSON.stringify(h)).not.toContain("mx_global_key");
    expect(h.cookie).toBeUndefined();
  });

  it("sends no project header for /auth/* and /projects", async () => {
    await GET(req("GET"), ctx("auth", "me"));
    await GET(req("GET"), ctx("projects"));
    for (const call of fetchMock.mock.calls) {
      const h = (call[1] as RequestInit).headers as Record<string, string>;
      expect(h.authorization).toBe(`Bearer ${TOKEN}`);
      expect(h["x-matrix-project"]).toBeUndefined();
    }
  });

  it("does not expose the API login/logout (their responses carry the token)", async () => {
    expect((await POST(req("POST", sameOrigin, "{}"), ctx("auth", "login"))).status).toBe(404);
    expect((await POST(req("POST", sameOrigin, "{}"), ctx("auth", "logout"))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("clears the cookies when the API answers 401", async () => {
    fetchMock.mockResolvedValueOnce(new Response('{"detail":"invalid or missing credentials"}', { status: 401 }));
    const res = await GET(req("GET"), ctx("traces"));
    expect(res.status).toBe(401);
    const cookies = res.headers.getSetCookie();
    expect(cookies.some((c) => c.startsWith("matrix_session=;") && c.includes("Max-Age=0"))).toBe(true);
    expect(cookies.some((c) => c.startsWith("matrix_project=;"))).toBe(true);
  });

  it("never puts the token in the response, even if the API echoes it", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('{"ok":true}', { status: 200, headers: { authorization: `Bearer ${TOKEN}`, "x-matrix-token": TOKEN } }),
    );
    const res = await GET(req("GET"), ctx("traces"));
    for (const [, value] of res.headers) expect(value).not.toContain(TOKEN);
    expect(res.headers.get("set-cookie")).toBeNull();
  });
});

describe("trusted client-IP header", () => {
  it("is not sent unless MATRIX_TRUSTED_PROXY_HEADER is set", async () => {
    await GET(req("GET", { "x-forwarded-for": "203.0.113.9", "x-real-ip": "6.6.6.6" }), ctx("traces"));
    const h = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(h["x-forwarded-for"]).toBeUndefined();
    expect(h["x-real-ip"]).toBeUndefined();
  });

  it("discards the browser's value and writes the IP the Next server sees", async () => {
    vi.stubEnv("MATRIX_TRUSTED_PROXY_HEADER", "X-Real-IP");
    vi.stubEnv("MATRIX_WEB_TRUST_FORWARDED_FOR", "1");
    // The browser sent x-real-ip itself and a spoofed first X-Forwarded-For entry; Next appended
    // the real peer as the last one.
    await GET(req("GET", { "x-real-ip": "6.6.6.6", "x-forwarded-for": "9.9.9.9, 203.0.113.9" }), ctx("traces"));
    const h = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(h["x-real-ip"]).toBe("203.0.113.9");
  });

  it("without MATRIX_WEB_TRUST_FORWARDED_FOR it sends nothing: X-Forwarded-For is client-controlled", async () => {
    vi.stubEnv("MATRIX_TRUSTED_PROXY_HEADER", "x-real-ip");
    await GET(req("GET", { "x-real-ip": "6.6.6.6", "x-forwarded-for": "203.0.113.9" }), ctx("traces"));
    const h = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(h["x-real-ip"]).toBeUndefined();
  });

  it("sends nothing (not the browser's value) when the peer address is unknown", async () => {
    vi.stubEnv("MATRIX_TRUSTED_PROXY_HEADER", "x-real-ip");
    vi.stubEnv("MATRIX_WEB_TRUST_FORWARDED_FOR", "1");
    await GET(req("GET", { "x-real-ip": "6.6.6.6" }), ctx("traces"));
    const h = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(h["x-real-ip"]).toBeUndefined();
  });

  it("rejects a malformed last entry", async () => {
    vi.stubEnv("MATRIX_TRUSTED_PROXY_HEADER", "x-real-ip");
    vi.stubEnv("MATRIX_WEB_TRUST_FORWARDED_FOR", "1");
    await GET(req("GET", { "x-forwarded-for": "203.0.113.9, not-an-ip" }), ctx("traces"));
    const h = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(h["x-real-ip"]).toBeUndefined();
  });
});

describe("headers", () => {
  it("propagates security headers and never returns the token or authorization", async () => {
    const res = await GET(req("GET", { authorization: "Bearer client-supplied" }), ctx("artifacts", "a1"));
    expect(res.headers.get("content-type")).toBe("application/json");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="a.bin"');
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("content-security-policy")).toBe("sandbox");
    expect(res.headers.get("cache-control")).toBe("private, max-age=60");
    expect(res.headers.get("authorization")).toBeNull();
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("x-internal")).toBeNull();
    for (const [, value] of res.headers) expect(value).not.toContain(TOKEN);
    // The cookie's token is injected upstream; the client's own authorization is not forwarded.
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("passes Retry-After through (rate limit and busy answers)", async () => {
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 429, headers: { "retry-after": "30" } }));
    const res = await POST(req("POST", sameOrigin, "{}"), ctx("auth", "password"));
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("30");
  });

  it("answers 502 when the API is unreachable", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    expect((await GET(req("GET"), ctx("traces"))).status).toBe(502);
  });
});
