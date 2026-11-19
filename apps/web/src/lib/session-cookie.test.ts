import { afterEach, describe, expect, it, vi } from "vitest";
import {
  clearSessionCookies,
  lastProjectFor,
  parseCookies,
  projectCookieName,
  sessionCookieConflict,
  sessionCookieName,
  lifetimeSeconds,
  safeNext,
  serializeCookie,
  sessionFromCookieHeader,
  setSessionCookies,
} from "./session-cookie";

const TOKEN = "mxs_abcdefghijklmnopqrstuvwxyz0123456789-_AB";

afterEach(() => vi.unstubAllEnvs());

describe("safeNext", () => {
  it.each(["/", "/traces", "/traces?name=a%20b&status=error", "/traces/abc#x", "/settings/audit?action=key.create"])(
    "keeps the relative path %s",
    (p) => expect(safeNext(p)).toBe(p),
  );

  it.each([
    "//evil.example",
    "//evil.example/path",
    "/\\evil.example",
    "\\\\evil.example",
    "https://evil.example",
    "http://evil.example/x",
    "javascript:alert(1)",
    "evil.example",
    "traces",
    "",
    "/a\nb",
    "/a\u0000b",
    "/%2F%2Fevil.example/../..//evil.example".replace("%2F", "/").replace("%2F", "/"),
    "/login",
    "/login?next=/x",
    "/api/proxy/traces",
    "/api",
  ])("falls back to / for %j", (p) => expect(safeNext(p)).toBe("/"));

  it("falls back for non-strings and absurd lengths", () => {
    expect(safeNext(undefined)).toBe("/");
    expect(safeNext(["/a"])).toBe("/");
    expect(safeNext("/" + "a".repeat(3000))).toBe("/");
  });
});

describe("cookies", () => {
  it("are HttpOnly, SameSite=Lax, Path=/ and not Secure in development", () => {
    vi.stubEnv("NODE_ENV", "development");
    const c = serializeCookie("matrix_session", TOKEN, 3600);
    expect(c).toContain("HttpOnly");
    expect(c).toContain("SameSite=Lax");
    expect(c).toContain("Path=/");
    expect(c).toContain("Max-Age=3600");
    expect(c).not.toContain("Secure");
  });

  it("are Secure in production unless explicitly opted out", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(serializeCookie("a", "b", 60)).toContain("Secure");
    vi.stubEnv("MATRIX_INSECURE_COOKIES", "1");
    expect(serializeCookie("a", "b", 60)).not.toContain("Secure");
  });

  it("set both cookies HttpOnly and clear them with Max-Age=0", () => {
    const h = new Headers();
    setSessionCookies(h, TOKEN, "p1", 600);
    const set = h.getSetCookie();
    expect(set).toHaveLength(2);
    expect(set.every((c) => c.includes("HttpOnly"))).toBe(true);
    const out = new Headers();
    clearSessionCookies(out);
    expect(out.getSetCookie().every((c) => c.includes("Max-Age=0"))).toBe(true);
  });

  it("reads the session and ignores malformed values", () => {
    expect(sessionFromCookieHeader(`matrix_session=${TOKEN}; matrix_project=p_1`)).toEqual({ token: TOKEN, projectId: "p_1" });
    expect(sessionFromCookieHeader(`matrix_session=${TOKEN}`)).toEqual({ token: TOKEN, projectId: null });
    expect(sessionFromCookieHeader(`matrix_session=${TOKEN}; matrix_project=a b`)?.projectId).toBeNull();
    expect(sessionFromCookieHeader("matrix_session=mx_apikey_value_xxxxxxxx")).toBeNull();
    expect(sessionFromCookieHeader("matrix_session=mxs_x\r\nX-Evil: 1")).toBeNull();
    expect(sessionFromCookieHeader(null)).toBeNull();
  });

  it("derives the lifetime from expires_at, bounded", () => {
    const now = Date.parse("2026-11-10T00:00:00Z");
    expect(lifetimeSeconds("2026-11-10T01:00:00Z", now)).toBe(3600);
    expect(lifetimeSeconds("2026-11-10T00:00:05Z", now)).toBe(60);
    expect(lifetimeSeconds("2030-01-01T00:00:00Z", now)).toBe(31 * 86400);
    expect(lifetimeSeconds("garbage", now)).toBeNull();
    expect(lifetimeSeconds(undefined, now)).toBeNull();
  });
});

describe("__Host- cookie names", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("are prefixed only when Secure is on", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(sessionCookieName()).toBe("__Host-matrix_session");
    expect(projectCookieName()).toBe("__Host-matrix_project");
    const h = new Headers();
    setSessionCookies(h, TOKEN, "p_1", 3600);
    for (const c of h.getSetCookie()) {
      expect(c).toMatch(/^__Host-/);
      expect(c).toMatch(/Secure/);
      expect(c).toMatch(/Path=\//);
      expect(c).not.toMatch(/Domain/i);
    }
    vi.stubEnv("MATRIX_INSECURE_COOKIES", "1");
    expect(sessionCookieName()).toBe("matrix_session");
  });

  it("in production only the prefixed cookie is a session", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(sessionFromCookieHeader(`matrix_session=${TOKEN}`)).toBeNull();
    expect(sessionFromCookieHeader(`matrix_session=x; __Host-matrix_session=${TOKEN}`)?.token).toBe(TOKEN);
  });

  it("drops duplicated names and refuses a duplicated session cookie", () => {
    expect(parseCookies("a=1; b=2; a=3")).toEqual({ b: "2" });
    const dup = `matrix_session=${TOKEN}; matrix_session=mxs_planted_token_000000000`;
    expect(sessionCookieConflict(dup)).toBe(true);
    expect(sessionFromCookieHeader(dup)).toBeNull();
    expect(sessionFromCookieHeader(`matrix_session=${TOKEN}; matrix_project=a; matrix_project=b`)).toBeNull();
  });

  it("clearing in production also clears the legacy unprefixed names", () => {
    vi.stubEnv("NODE_ENV", "production");
    const h = new Headers();
    clearSessionCookies(h);
    const names = h.getSetCookie().map((c) => c.split("=")[0]);
    expect(names).toEqual(expect.arrayContaining(["__Host-matrix_session", "__Host-matrix_project", "matrix_session", "matrix_project"]));
  });

  it("the last-project cookie only counts for the same user", () => {
    expect(lastProjectFor("matrix_last_project=u1~p_1", "u1")).toBe("p_1");
    expect(lastProjectFor("matrix_last_project=u1~p_1", "u2")).toBeNull();
    expect(lastProjectFor("matrix_last_project=u1~p 1", "u1")).toBeNull();
  });
});
