// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DELETE, GET, POST } from "./route";

const KEY = "mx_secret_key_value";
const HOST = "matrix.local:3000";

function ctx(...path: string[]) {
  return { params: Promise.resolve({ path }) };
}

function req(method: string, headers: Record<string, string> = {}, body?: BodyInit) {
  return new Request(`http://${HOST}/api/proxy/x`, {
    method,
    headers: { host: HOST, ...headers },
    body,
    ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
  } as RequestInit);
}

const sameOrigin = { origin: `http://${HOST}` };

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("MATRIX_API_URL", "http://api:8000");
  vi.stubEnv("MATRIX_API_KEY", KEY);
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
        authorization: `Bearer ${KEY}`,
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
    const r = new Request(`http://${HOST}/api/proxy/traces?limit=5`, { headers: { host: HOST } });
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

describe("headers", () => {
  it("propagates security headers and never returns the key or authorization", async () => {
    const res = await GET(req("GET", { authorization: "Bearer client-supplied" }), ctx("artifacts", "a1"));
    expect(res.headers.get("content-type")).toBe("application/json");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="a.bin"');
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("content-security-policy")).toBe("sandbox");
    expect(res.headers.get("cache-control")).toBe("private, max-age=60");
    expect(res.headers.get("authorization")).toBeNull();
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("x-internal")).toBeNull();
    for (const [, value] of res.headers) expect(value).not.toContain(KEY);
    // The server key is injected upstream; the client's own authorization is not forwarded.
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${KEY}`);
  });

  it("injects no credential when MATRIX_API_KEY is unset", async () => {
    vi.stubEnv("MATRIX_API_KEY", "");
    await GET(req("GET"), ctx("traces"));
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBeUndefined();
  });

  it("answers 502 when the API is unreachable", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    expect((await GET(req("GET"), ctx("traces"))).status).toBe(502);
  });
});
