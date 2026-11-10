// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { middleware } from "./middleware";

const TOKEN = "mxs_session_token_for_tests_0123456789";
const req = (path: string, cookie?: string) =>
  new NextRequest(`http://matrix.local:3000${path}`, { headers: cookie ? { cookie } : {} });

describe("middleware", () => {
  it("redirects pages without a session to /login with a validated next", () => {
    const res = middleware(req("/traces?status=error"));
    expect(res.status).toBe(307);
    const loc = new URL(res.headers.get("location")!);
    expect(loc.pathname).toBe("/login");
    expect(loc.searchParams.get("next")).toBe("/traces?status=error");
  });

  it("omits next for the home page", () => {
    expect(new URL(middleware(req("/")).headers.get("location")!).search).toBe("");
  });

  it("does not let a crafted path turn into an open redirect", () => {
    const loc = new URL(middleware(req("//evil.example/x")).headers.get("location")!);
    expect(loc.origin).toBe("http://matrix.local:3000");
    expect(loc.searchParams.get("next") ?? "/").not.toMatch(/^\/\//);
  });

  it("lets /login, /api/auth/login and /api/auth/expired through without a session", () => {
    for (const p of ["/login", "/api/auth/login", "/api/auth/expired"]) {
      expect(middleware(req(p)).headers.get("location")).toBeNull();
      expect(middleware(req(p)).status).toBe(200);
    }
  });

  it("answers 401 JSON (not a redirect) for API routes without a session", async () => {
    for (const p of ["/api/proxy/traces", "/api/auth/me", "/api/auth/logout", "/api/auth/switch-project"]) {
      const res = middleware(req(p));
      expect(res.status).toBe(401);
      expect(res.headers.get("location")).toBeNull();
    }
  });

  it("passes requests with a well-formed session cookie, and rejects a malformed one", () => {
    expect(middleware(req("/traces", `matrix_session=${TOKEN}`)).status).toBe(200);
    expect(middleware(req("/traces", "matrix_session=mx_key_not_session_xxxx")).status).toBe(307);
    expect(middleware(req("/traces", "matrix_project=abc")).status).toBe(307);
  });

  it("does not trust a client-supplied x-matrix-path", () => {
    const r = new NextRequest("http://matrix.local:3000/traces", { headers: { cookie: `matrix_session=${TOKEN}`, "x-matrix-path": "//evil" } });
    const res = middleware(r);
    expect(res.headers.get("x-middleware-request-x-matrix-path")).toBe("/traces");
  });
});
