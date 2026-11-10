// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST as login } from "./login/route";
import { POST as logout } from "./logout/route";
import { POST as switchProject } from "./switch-project/route";
import { GET as expired } from "./expired/route";

const HOST = "matrix.local:3000";
const TOKEN = "mxs_session_token_for_tests_0123456789";
const P1 = "a1b2c3d4e5f60718293a4b5c6d7e8f90";
const P2 = "0f9e8d7c6b5a49382716059483726150";

function post(path: string, body: unknown, headers: Record<string, string> = {}) {
  return new Request(`http://${HOST}/api/auth/${path}`, {
    method: "POST",
    headers: { host: HOST, origin: `http://${HOST}`, "content-type": "application/json", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

let fetchMock: ReturnType<typeof vi.fn>;
const apiLogin = (over: Record<string, unknown> = {}) =>
  new Response(
    JSON.stringify({
      token: TOKEN,
      expires_at: new Date(Date.now() + 3600_000).toISOString(),
      user: { id: "u1", email: "a@b.co", name: "A", created_at: "", last_login_at: null },
      memberships: [
        { project: { id: P1, name: "One" }, role: "owner" },
        { project: { id: P2, name: "Two" }, role: "viewer" },
      ],
      ...over,
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );

beforeEach(() => {
  vi.stubEnv("MATRIX_API_URL", "http://api:8000");
  vi.stubEnv("MATRIX_MOCK", "");
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("MATRIX_TRUSTED_PROXY_HEADER", "x-real-ip");
  vi.stubEnv("MATRIX_WEB_TRUST_FORWARDED_FOR", "1");
  fetchMock = vi.fn(async () => apiLogin());
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("POST /api/auth/login", () => {
  it("sets HttpOnly cookies and never returns the token to the browser", async () => {
    const res = await login(post("login", { email: "a@b.co", password: "pw-pw-pw-pw-1", next: "/traces?x=1" }, { "x-forwarded-for": "9.9.9.9, 203.0.113.5" }));
    expect(res.status).toBe(200);
    const text = await res.text();
    expect(text).not.toContain(TOKEN);
    expect(JSON.parse(text)).toMatchObject({ ok: true, next: "/traces?x=1", project_id: P1 });
    const cookies = res.headers.getSetCookie();
    const session = cookies.find((c) => c.startsWith("matrix_session="))!;
    expect(session).toContain(TOKEN);
    expect(session).toMatch(/HttpOnly/);
    expect(session).toMatch(/SameSite=Lax/);
    expect(session).toMatch(/Secure/);
    expect(session).toMatch(/Path=\//);
    expect(session).toMatch(/Max-Age=3[56]\d\d/);
    expect(cookies.find((c) => c.startsWith("matrix_project="))).toContain(`matrix_project=${P1}`);
    expect(cookies.find((c) => c.startsWith("matrix_project="))).toMatch(/HttpOnly/);
    for (const [, v] of res.headers) if (!/^matrix_/.test(v)) expect(v).not.toContain(TOKEN);
    // Upstream: the client IP is the one Next saw, written to the trusted header.
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["x-real-ip"]).toBe("203.0.113.5");
    expect((init.headers as Record<string, string>).authorization).toBeUndefined();
  });

  it("keeps the previously selected project when still a member", async () => {
    const res = await login(post("login", { email: "a@b.co", password: "pw" }, { cookie: `matrix_project=${P2}` }));
    expect((await res.json()).project_id).toBe(P2);
  });

  it("neutralises a hostile next", async () => {
    for (const next of ["//evil.example", "https://evil.example", "/\\evil.example", "javascript:1"]) {
      const res = await login(post("login", { email: "a@b.co", password: "pw", next }));
      expect((await res.json()).next).toBe("/");
    }
  });

  it("answers a generic 401 without cookies", async () => {
    fetchMock.mockResolvedValueOnce(new Response('{"detail":"invalid email or password (user u_123 locked)"}', { status: 401 }));
    const res = await login(post("login", { email: "a@b.co", password: "bad" }));
    expect(res.status).toBe(401);
    const body = await res.text();
    expect(body).toBe('{"detail":"Invalid email or password."}');
    expect(res.headers.getSetCookie()).toHaveLength(0);
  });

  it.each([429, 503])("passes %i with the Retry-After seconds and nothing else", async (status) => {
    fetchMock.mockResolvedValueOnce(new Response('{"detail":"internal secret detail"}', { status, headers: { "retry-after": "42" } }));
    const res = await login(post("login", { email: "a@b.co", password: "x" }));
    expect(res.status).toBe(status);
    const body = await res.json();
    expect(body.retry_after).toBe(42);
    expect(JSON.stringify(body)).not.toContain("secret");
    expect(res.headers.getSetCookie()).toHaveLength(0);
  });

  it("does not leak backend failures", async () => {
    fetchMock.mockResolvedValueOnce(new Response("Traceback: password hash …", { status: 500 }));
    const res = await login(post("login", { email: "a@b.co", password: "x" }));
    expect(res.status).toBe(502);
    expect(await res.text()).not.toContain("Traceback");
    fetchMock.mockRejectedValueOnce(new TypeError("connect ECONNREFUSED 10.0.0.5:8000"));
    const down = await login(post("login", { email: "a@b.co", password: "x" }));
    expect(down.status).toBe(502);
    expect(await down.text()).not.toContain("10.0.0.5");
  });

  it("refuses a malformed token or expiry from the API", async () => {
    fetchMock.mockResolvedValueOnce(apiLogin({ token: "mx_not_a_session_token_zzzz" }));
    expect((await login(post("login", { email: "a@b.co", password: "x" }))).status).toBe(502);
    fetchMock.mockResolvedValueOnce(apiLogin({ expires_at: "soon" }));
    expect((await login(post("login", { email: "a@b.co", password: "x" }))).status).toBe(502);
  });

  it("refuses cross-origin posts and bad bodies without calling the API", async () => {
    expect((await login(post("login", {}, { origin: "http://evil.example" }))).status).toBe(403);
    expect((await login(post("login", "not json"))).status).toBe(400);
    expect((await login(post("login", { email: "a@b.co" }))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("POST /api/auth/logout", () => {
  it("revokes upstream and clears the cookies", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const res = await logout(post("logout", {}, { cookie: `matrix_session=${TOKEN}; matrix_project=${P1}` }));
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
    expect(res.headers.getSetCookie().every((c) => c.includes("Max-Age=0"))).toBe(true);
    expect((await res.json()).revoked).toBe(true);
  });

  it("clears the cookies even when the API fails", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    const res = await logout(post("logout", {}, { cookie: `matrix_session=${TOKEN}` }));
    expect(res.status).toBe(200);
    expect(res.headers.getSetCookie().some((c) => c.startsWith("matrix_session=;"))).toBe(true);
    expect((await res.json()).revoked).toBe(false);
  });

  it("clears the cookies when the API answers 500, and with no session at all", async () => {
    fetchMock.mockResolvedValueOnce(new Response("boom", { status: 500 }));
    const a = await logout(post("logout", {}, { cookie: `matrix_session=${TOKEN}` }));
    expect(a.headers.getSetCookie().length).toBe(2);
    const b = await logout(post("logout", {}));
    expect(b.headers.getSetCookie().length).toBe(2);
  });
});

describe("POST /api/auth/switch-project", () => {
  const me = () => new Response(JSON.stringify({ user: {}, memberships: [{ project: { id: P1, name: "One" }, role: "owner" }] }));

  it("switches only to a project the API lists for the user", async () => {
    fetchMock.mockResolvedValueOnce(me());
    const ok = await switchProject(post("switch-project", { project_id: P1 }, { cookie: `matrix_session=${TOKEN}` }));
    expect(ok.status).toBe(200);
    expect(ok.headers.getSetCookie().find((c) => c.startsWith("matrix_project="))).toContain(P1);
    expect(ok.headers.getSetCookie().find((c) => c.startsWith("matrix_session="))).toBeUndefined();
    fetchMock.mockResolvedValueOnce(me());
    const no = await switchProject(post("switch-project", { project_id: P2 }, { cookie: `matrix_session=${TOKEN}` }));
    expect(no.status).toBe(404);
    expect(no.headers.getSetCookie()).toHaveLength(0);
  });

  it("rejects ids that could not be a project and missing sessions", async () => {
    expect((await switchProject(post("switch-project", { project_id: "a b\r\nx" }, { cookie: `matrix_session=${TOKEN}` }))).status).toBe(400);
    expect((await switchProject(post("switch-project", { project_id: P1 }))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("GET /api/auth/expired", () => {
  const get = (qs: string, cookie?: string) =>
    expired(new Request(`http://${HOST}/api/auth/expired${qs}`, { headers: { host: HOST, ...(cookie ? { cookie } : {}) } }));

  it("clears a dead session and redirects to /login with a safe next", async () => {
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 401 }));
    const res = await get("?next=/traces%3Fa%3D1", `matrix_session=${TOKEN}`);
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/login?next=%2Ftraces%3Fa%3D1");
    expect(res.headers.getSetCookie().some((c) => c.startsWith("matrix_session=;"))).toBe(true);
  });

  it("does not sign anybody out when the session is still valid", async () => {
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    const res = await get("?next=/traces", `matrix_session=${TOKEN}`);
    expect(res.headers.get("location")).toBe("/traces");
    expect(res.headers.getSetCookie()).toHaveLength(0);
  });

  it("never redirects to an external next", async () => {
    const res = await get("?next=//evil.example", undefined);
    expect(res.headers.get("location")).toBe("/login?next=%2F");
  });
});
