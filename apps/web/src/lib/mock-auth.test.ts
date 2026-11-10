import { beforeEach, describe, expect, it } from "vitest";
import { ApiError } from "./api-error";
import { mockGet, mockSend, NO_CONTENT } from "./mock-api";
import { DEMO_EMAIL, DEMO_PASSWORD, PROJECT_A, PROJECT_B, resetMockAuth } from "./mock-auth";
import type { AuthSession, MeResponse } from "./types";

type Login = { token: string; expires_at: string; memberships: MeResponse["memberships"] };

async function login(email = DEMO_EMAIL, password = DEMO_PASSWORD) {
  return mockSend<Login>("POST", "/api/v1/auth/login", { email, password }, { token: null, projectId: null });
}
const ctx = (token: string, projectId: string | null = PROJECT_A) => ({ token, projectId });
const status = async (p: Promise<unknown>) => ((await p.then(() => null, (e) => e)) as ApiError | null)?.status ?? 0;

beforeEach(() => resetMockAuth());

describe("mock login", () => {
  it("returns a token, expiry and the demo memberships (two projects, varied roles)", async () => {
    const out = await login();
    expect(out.token).toMatch(/^mxs_[A-Za-z0-9_-]{20,}$/);
    expect(Date.parse(out.expires_at)).toBeGreaterThan(Date.now());
    expect(out.memberships.map((m) => [m.project.id, m.role]).sort()).toEqual(
      [[PROJECT_A, "owner"], [PROJECT_B, "viewer"]].sort(),
    );
  });

  it("answers 401 for a wrong password and for an unknown user alike", async () => {
    const a = await login(DEMO_EMAIL, "wrong").catch((e: ApiError) => e);
    const b = await login("nobody@example.com", "whatever-12345").catch((e: ApiError) => e);
    expect(a).toBeInstanceOf(ApiError);
    expect((a as ApiError).status).toBe(401);
    expect((b as ApiError).status).toBe(401);
    expect((a as ApiError).body).toBe((b as ApiError).body);
  });

  it("locks out with 429 and Retry-After after repeated failures", async () => {
    for (let i = 0; i < 5; i++) await login(DEMO_EMAIL, "wrong").catch(() => null);
    const e = (await login(DEMO_EMAIL, DEMO_PASSWORD).catch((x: ApiError) => x)) as ApiError;
    expect(e.status).toBe(429);
    expect(e.retryAfter).toBeGreaterThan(0);
  });
});

describe("mock authorization", () => {
  it("requires a valid session and, for data routes, a member project", async () => {
    expect(await status(mockGet("/api/v1/traces", {}, { token: null, projectId: PROJECT_A }))).toBe(401);
    expect(await status(mockGet("/api/v1/traces", {}, ctx("mxs_forged_token_value_0000")))).toBe(401);
    const { token } = await login();
    expect(await status(mockGet("/api/v1/traces", {}, ctx(token, null)))).toBe(400);
    expect(await status(mockGet("/api/v1/traces", {}, ctx(token, "ffffffffffffffffffffffffffffffff")))).toBe(404);
    expect((await mockGet<{ items: unknown[] }>("/api/v1/traces", {}, ctx(token))).items.length).toBeGreaterThan(0);
  });

  it("viewer reads but cannot write or administer; admin pages say 403", async () => {
    const { token } = await login();
    const viewer = ctx(token, PROJECT_B);
    expect((await mockGet<{ items: unknown[] }>("/api/v1/traces", {}, viewer)).items).toBeDefined();
    expect(await status(mockSend("POST", "/api/v1/datasets", { name: "x" }, viewer))).toBe(403);
    expect(await status(mockGet(`/api/v1/projects/${PROJECT_B}/keys`, {}, viewer))).toBe(403);
    expect(await status(mockGet(`/api/v1/projects/${PROJECT_B}/members`, {}, viewer))).toBe(403);
  });

  it("logout revokes the session", async () => {
    const { token } = await login();
    expect(await mockSend("POST", "/api/v1/auth/logout", undefined, ctx(token))).toBe(NO_CONTENT);
    expect(await status(mockGet("/api/v1/auth/me", {}, ctx(token)))).toBe(401);
  });
});

describe("mock account", () => {
  it("changes the password: 403 for a wrong current one, 422 under 12 characters, others revoked", async () => {
    const { token } = await login();
    const c = ctx(token);
    expect(await status(mockSend("POST", "/api/v1/auth/password", { current_password: "nope", new_password: "a-long-new-password" }, c))).toBe(403);
    expect(await status(mockSend("POST", "/api/v1/auth/password", { current_password: DEMO_PASSWORD, new_password: "short" }, c))).toBe(422);
    expect(await mockSend("POST", "/api/v1/auth/password", { current_password: DEMO_PASSWORD, new_password: "a-long-new-password" }, c)).toBe(NO_CONTENT);
    const { items } = await mockGet<{ items: AuthSession[] }>("/api/v1/auth/sessions", {}, c);
    expect(items).toHaveLength(1);
    expect(items[0].current).toBe(true);
    await login(DEMO_EMAIL, "a-long-new-password");
  });

  it("lists sessions with the current one marked, and revokes another", async () => {
    const { token } = await login();
    const c = ctx(token);
    const { items } = await mockGet<{ items: AuthSession[] }>("/api/v1/auth/sessions", {}, c);
    expect(items.filter((s) => s.current)).toHaveLength(1);
    const other = items.find((s) => !s.current)!;
    expect(await mockSend("DELETE", `/api/v1/auth/sessions/${other.id}`, undefined, c)).toBe(NO_CONTENT);
    expect(await status(mockSend("DELETE", `/api/v1/auth/sessions/${other.id}`, undefined, c))).toBe(404);
  });
});

describe("mock members, keys, egress, audit", () => {
  it("adds, updates and removes members with the contract's errors", async () => {
    const c = ctx((await login()).token);
    const base = `/api/v1/projects/${PROJECT_A}/members`;
    const added = await mockSend<{ role: string }>("POST", base, { email: "dario@example.com", role: "member" }, c);
    expect(added.role).toBe("member");
    expect(await status(mockSend("POST", base, { email: "dario@example.com", role: "member" }, c))).toBe(409);
    expect(await status(mockSend("POST", base, { email: "ghost@example.com", role: "member" }, c))).toBe(404);
    await mockSend("PATCH", `${base}/u_dario`, { role: "viewer" }, c);
    expect(await mockSend("DELETE", `${base}/u_dario`, undefined, c)).toBe(NO_CONTENT);
    expect(await status(mockSend("DELETE", `${base}/u_dario`, undefined, c))).toBe(404);
    // Sole owner can neither be demoted nor removed.
    expect(await status(mockSend("PATCH", `${base}/u_demo`, { role: "admin" }, c))).toBe(409);
    expect(await status(mockSend("DELETE", `${base}/u_demo`, undefined, c))).toBe(409);
  });

  it("shows the key secret once and revokes", async () => {
    const c = ctx((await login()).token);
    const base = `/api/v1/projects/${PROJECT_A}/keys`;
    const made = await mockSend<{ id: string; prefix: string; api_key: string }>("POST", base, { name: "t", scopes: ["read", "ingest"] }, c);
    expect(made.api_key).toMatch(/^mx_[0-9a-f]{8}_/);
    const { items } = await mockGet<{ items: Record<string, unknown>[] }>(base, {}, c);
    const row = items.find((k) => k.id === made.id)!;
    expect(JSON.stringify(row)).not.toContain(made.api_key.split("_")[2]);
    expect(row.scopes).toEqual(["ingest", "read"]);
    expect(await status(mockSend("POST", base, { name: "t", scopes: [] }, c))).toBe(422);
    expect(await status(mockSend("POST", base, { name: "t", scopes: ["admin"] }, c))).toBe(422);
    await mockSend("DELETE", `${base}/${made.id}`, undefined, c);
    const after = await mockGet<{ items: { id: string; revoked_at: string | null }[] }>(base, {}, c);
    expect(after.items.find((k) => k.id === made.id)!.revoked_at).not.toBeNull();
  });

  it("validates egress hosts like the contract and rejects duplicates", async () => {
    const c = ctx((await login()).token);
    const base = `/api/v1/projects/${PROJECT_A}/egress-hosts`;
    await mockSend("POST", base, { host: "*.example.org" }, c);
    expect(await status(mockSend("POST", base, { host: "*.example.org" }, c))).toBe(409);
    for (const bad of ["*", "127.0.0.1", "localhost", "a.*.b.com", "https://x.com"]) {
      expect(await status(mockSend("POST", base, { host: bad }, c))).toBe(422);
    }
  });

  it("pages and filters the audit log", async () => {
    const c = ctx((await login()).token);
    const base = `/api/v1/projects/${PROJECT_A}/audit-log`;
    const first = await mockGet<{ items: { action: string }[]; next_cursor: string | null }>(base, { limit: 20 }, c);
    expect(first.items).toHaveLength(20);
    expect(first.next_cursor).toBe("20");
    const filtered = await mockGet<{ items: { action: string }[] }>(base, { action: "key.create" }, c);
    expect(filtered.items.length).toBeGreaterThan(0);
    expect(filtered.items.every((e) => e.action === "key.create")).toBe(true);
  });

  it("creates a project the user owns", async () => {
    const c = ctx((await login()).token, null);
    const made = await mockSend<{ project: { id: string }; role: string }>("POST", "/api/v1/projects", { name: "Fresh" }, c);
    expect(made.role).toBe("owner");
    expect(await status(mockSend("POST", "/api/v1/projects", { name: "fresh" }, c))).toBe(409);
    const me = await mockGet<{ items: unknown[] }>("/api/v1/projects", {}, c);
    expect(me.items).toHaveLength(3);
  });
});
