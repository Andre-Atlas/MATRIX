// In-memory users, sessions, projects, members, keys, egress hosts and audit log for MATRIX_MOCK=1.
// Mirrors server/src/matrix_server/api/{auth,projects,egress}.py closely enough for the UI: same
// shapes, status codes and role rules. State lives on globalThis so route handlers and Server
// Components share it in one process. Demo login: demo@example.com / demo-password-123.
import { createHash, randomBytes } from "node:crypto";
import { ApiError } from "./api-error";
import { validateEgressEntry } from "./egress-host";
import {
  KEY_SCOPES,
  ROLES,
  roleAtLeast,
  roleRank,
  type ApiKeyItem,
  type AuditEntry,
  type AuthSession,
  type EgressHost,
  type KeyScope,
  type Membership,
  type Role,
  type User,
} from "./types";

export const NO_CONTENT = Symbol("no-content");
export type MockCtx = { token: string | null; projectId: string | null; ip?: string | null; userAgent?: string | null };
type Params = Record<string, string | number | undefined>;

type MockUser = User & { password: string };
type MockSession = { id: string; token: string; user_id: string; created_at: string; last_seen_at: string; expires_at: string; user_agent: string | null; ip: string | null };
type MemberRow = { user_id: string; project_id: string; role: Role; added_at: string };
type KeyRow = ApiKeyItem & { project_id: string };
type EgressRow = EgressHost & { project_id: string };
type AuditRow = AuditEntry & { project_id: string };
type Store = {
  users: MockUser[];
  sessions: MockSession[];
  projects: { id: string; name: string }[];
  members: MemberRow[];
  keys: KeyRow[];
  egress: EgressRow[];
  audit: AuditRow[];
  failures: Map<string, { count: number; lockedUntil: number }>;
};

export const DEMO_EMAIL = "demo@example.com";
export const DEMO_PASSWORD = "demo-password-123";
export const PROJECT_A = "a1b2c3d4e5f60718293a4b5c6d7e8f90";
export const PROJECT_B = "0f9e8d7c6b5a49382716059483726150";

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
const MAX_FAILURES = 5;
const LOCK_SECONDS = 30;
const PASSWORD_MIN = 12;
const PASSWORD_MAX = 256;

let counter = 0;
const newId = () => randomBytes(16).toString("hex") + (counter++ % 10);
const iso = (ms: number) => new Date(ms).toISOString();
const sha256 = (s: string) => createHash("sha256").update(s).digest("hex");
const bad = (detail: string, status = 422): never => {
  throw new ApiError(status, JSON.stringify({ detail }));
};
const isObject = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);

function seed(now = Date.now()): Store {
  const mkUser = (id: string, email: string, name: string, password: string, createdDays: number): MockUser => ({
    id,
    email,
    name,
    password,
    created_at: iso(now - createdDays * DAY),
    last_login_at: iso(now - createdDays * HOUR),
  });
  const users = [
    mkUser("u_demo", DEMO_EMAIL, "Demo User", DEMO_PASSWORD, 40),
    mkUser("u_ana", "ana@example.com", "Ana Ribeiro", "ana-password-123", 38),
    mkUser("u_bruno", "bruno@example.com", "Bruno Tavares", "bruno-password-123", 21),
    mkUser("u_carla", "carla@example.com", "Carla Mendes", "carla-password-123", 9),
    mkUser("u_dario", "dario@example.com", "Dario Lima", "dario-password-123", 2),
  ];
  const member = (user_id: string, project_id: string, role: Role, ageDays: number): MemberRow => ({
    user_id,
    project_id,
    role,
    added_at: iso(now - ageDays * DAY),
  });
  const members = [
    member("u_demo", PROJECT_A, "owner", 40),
    member("u_ana", PROJECT_A, "admin", 38),
    member("u_bruno", PROJECT_A, "member", 21),
    member("u_carla", PROJECT_A, "viewer", 9),
    member("u_demo", PROJECT_B, "viewer", 12),
    member("u_ana", PROJECT_B, "owner", 12),
  ];
  const key = (id: string, project_id: string, prefix: string, name: string, scopes: KeyScope[], ageDays: number, used: number | null, revoked: boolean): KeyRow => ({
    id,
    project_id,
    prefix,
    name,
    scopes,
    created_at: iso(now - ageDays * DAY),
    created_by: "u_ana",
    last_used_at: used === null ? null : iso(now - used * MIN),
    revoked_at: revoked ? iso(now - 2 * DAY) : null,
  });
  const keys = [
    key("k_ingest_prod", PROJECT_A, "a1b2c3d4", "prod-ingest", ["ingest"], 30, 3, false),
    key("k_ci", PROJECT_A, "e5f60718", "ci-evals", ["read", "write"], 14, 95, false),
    key("k_old", PROJECT_A, "9a8b7c6d", "legacy-notebook", ["ingest", "read", "write"], 60, null, true),
    key("k_sandbox", PROJECT_B, "55aa66bb", "sandbox", ["read"], 5, 400, false),
  ];
  const host = (id: string, project_id: string, h: string, ageDays: number): EgressRow => ({
    id,
    project_id,
    host: h,
    created_at: iso(now - ageDays * DAY),
    created_by: "u_ana",
  });
  const egress = [
    host("h1", PROJECT_A, "api.openai.com", 30),
    host("h2", PROJECT_A, "*.slack.com", 12),
    host("h3", PROJECT_A, "hooks.example.com", 4),
    host("h4", PROJECT_B, "api.anthropic.com", 5),
  ];
  const actions: [string, string, string | null, Record<string, unknown>][] = [
    ["auth.login", "u_ana", "u_ana", { ip: "203.0.113.7" }],
    ["auth.login", "u_bruno", "u_bruno", { ip: "198.51.100.23" }],
    ["auth.login_failed", "u_carla", "u_carla", { ip: "198.51.100.99", locked: false }],
    ["key.create", "u_ana", "k_ci", { name: "ci-evals", scopes: ["read", "write"] }],
    ["member.add", "u_ana", "u_carla", { role: "viewer" }],
    ["member.update", "u_demo", "u_bruno", { from: "viewer", to: "member" }],
    ["egress.host_add", "u_ana", "hooks.example.com", {}],
    ["auth.logout", "u_bruno", "u_bruno", {}],
    ["key.revoke", "u_ana", "k_old", { prefix: "9a8b7c6d" }],
    ["egress.request", "u_demo", "api.openai.com", { purpose: "online-eval", allowed: true }],
  ];
  const audit: AuditRow[] = Array.from({ length: 64 }, (_, i) => {
    const [action, actor, target, detail] = actions[i % actions.length];
    return {
      id: `aud_${String(1000 - i).padStart(5, "0")}`,
      project_id: PROJECT_A,
      ts: iso(now - (i * 47 + 3) * MIN),
      actor: { kind: "user", id: actor },
      action,
      target,
      detail,
    };
  });
  const sessions: MockSession[] = [
    {
      id: sha256("other-laptop"),
      token: "mxs_other_laptop_not_a_real_token",
      user_id: "u_demo",
      created_at: iso(now - 3 * DAY),
      last_seen_at: iso(now - 5 * HOUR),
      expires_at: iso(now + 27 * DAY),
      user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Safari/605.1.15",
      ip: "203.0.113.45",
    },
    {
      id: sha256("phone"),
      token: "mxs_phone_not_a_real_token_value",
      user_id: "u_demo",
      created_at: iso(now - 9 * DAY),
      last_seen_at: iso(now - 2 * DAY),
      expires_at: iso(now + 21 * DAY),
      user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Mobile/15E148",
      ip: "198.51.100.12",
    },
  ];
  return { users, sessions, projects: [{ id: PROJECT_A, name: "Support Copilot" }, { id: PROJECT_B, name: "Research Sandbox" }], members, keys, egress, audit, failures: new Map() };
}

function store(): Store {
  const g = globalThis as unknown as { __matrixAuthStore?: Store };
  return (g.__matrixAuthStore ??= seed());
}

/** Test hook: start from a fresh seed. */
export function resetMockAuth(): void {
  (globalThis as unknown as { __matrixAuthStore?: Store }).__matrixAuthStore = undefined;
}

const publicUser = ({ password: _p, ...u }: MockUser): User => u;

function membershipsOf(s: Store, userId: string): Membership[] {
  return s.members
    .filter((m) => m.user_id === userId)
    .map((m) => ({ project: s.projects.find((p) => p.id === m.project_id)!, role: m.role }))
    .sort((a, b) => a.project.name.localeCompare(b.project.name));
}

function record(s: Store, projectId: string, actorId: string | null, action: string, target: string | null, detail: Record<string, unknown> = {}) {
  s.audit.unshift({ id: `aud_${newId()}`, project_id: projectId, ts: iso(Date.now()), actor: { kind: "user", id: actorId }, action, target, detail });
}

// ── authentication ────────────────────────────────────────────────────────────────────────

type Authed = { s: Store; user: MockUser; session: MockSession };

function authenticate(ctx: MockCtx): Authed {
  const s = store();
  const unauthorized = () => new ApiError(401, JSON.stringify({ detail: "invalid or missing credentials" }));
  if (!ctx.token) throw unauthorized();
  const session = s.sessions.find((x) => x.token === ctx.token);
  if (!session || Date.parse(session.expires_at) <= Date.now()) throw unauthorized();
  const user = s.users.find((u) => u.id === session.user_id);
  if (!user) throw unauthorized();
  session.last_seen_at = iso(Date.now());
  return { s, user, session };
}

function login(ctx: MockCtx, body: unknown) {
  const s = store();
  if (!isObject(body) || typeof body.email !== "string" || typeof body.password !== "string" || !body.email || !body.password) {
    bad("email and password are required");
  }
  const { email, password } = body as { email: string; password: string };
  const key = email.trim().toLowerCase();
  const fail = s.failures.get(key);
  if (fail && fail.lockedUntil > Date.now()) {
    throw new ApiError(429, JSON.stringify({ detail: "too many failed attempts, try again later" }), Math.ceil((fail.lockedUntil - Date.now()) / 1000));
  }
  const user = s.users.find((u) => u.email === key);
  if (!user || user.password !== password) {
    const f = fail ?? { count: 0, lockedUntil: 0 };
    f.count += 1;
    if (f.count >= MAX_FAILURES) {
      f.lockedUntil = Date.now() + LOCK_SECONDS * 1000;
      f.count = 0;
    }
    s.failures.set(key, f);
    throw new ApiError(401, JSON.stringify({ detail: "invalid email or password" }));
  }
  s.failures.delete(key);
  const token = `mxs_${randomBytes(32).toString("base64url")}`;
  const now = Date.now();
  user.last_login_at = iso(now);
  const session: MockSession = {
    id: sha256(token),
    token,
    user_id: user.id,
    created_at: iso(now),
    last_seen_at: iso(now),
    expires_at: iso(now + 30 * DAY),
    user_agent: ctx.userAgent ?? null,
    ip: ctx.ip ?? "127.0.0.1",
  };
  s.sessions.unshift(session);
  for (const m of s.members.filter((m) => m.user_id === user.id)) record(s, m.project_id, user.id, "auth.login", user.id, { ip: session.ip });
  return { token, expires_at: session.expires_at, user: publicUser(user), memberships: membershipsOf(s, user.id) };
}

// ── project scoping and roles ─────────────────────────────────────────────────────────────

function roleIn(s: Store, userId: string, projectId: string): Role | null {
  return s.members.find((m) => m.user_id === userId && m.project_id === projectId)?.role ?? null;
}

function requireRole(a: Authed, projectId: string, min: Role): Role {
  const role = roleIn(a.s, a.user.id, projectId);
  if (!role) throw new ApiError(404, JSON.stringify({ detail: "project not found" }));
  if (!roleAtLeast(role, min)) throw new ApiError(403, JSON.stringify({ detail: `requires role ${min}` }));
  return role;
}

const ADMIN_DATA = /^\/api\/v1\/(rules|online-evals)(\/|$)|^\/api\/v1\/prompts\/.*\/labels/;
const AUTH_OR_PROJECTS = /^\/api\/v1\/(auth\/|projects(\/|$))/;

/**
 * Authentication and role gate for every mock call made with a context (the web server always
 * passes one). Data routes use the project from the context (the X-Matrix-Project header).
 */
export function mockGate(method: string, path: string, ctx: MockCtx): void {
  const p = path.replace(/\/+$/, "");
  if (method === "POST" && p === "/api/v1/auth/login") return;
  const a = authenticate(ctx);
  if (AUTH_OR_PROJECTS.test(p)) return;
  if (!ctx.projectId) throw new ApiError(400, JSON.stringify({ detail: "X-Matrix-Project header required" }));
  const min: Role = method === "GET" ? "viewer" : ADMIN_DATA.test(p) ? "admin" : "member";
  requireRole(a, ctx.projectId, min);
}

// ── handlers ──────────────────────────────────────────────────────────────────────────────

export function authGet(ctx: MockCtx, path: string, params: Params): unknown {
  let m: RegExpExecArray | null;
  if (path === "/api/v1/auth/me") {
    const a = authenticate(ctx);
    return { user: publicUser(a.user), memberships: membershipsOf(a.s, a.user.id) };
  }
  if (path === "/api/v1/auth/sessions") {
    const a = authenticate(ctx);
    const items: AuthSession[] = a.s.sessions
      .filter((x) => x.user_id === a.user.id)
      .map((x) => ({ id: x.id, created_at: x.created_at, last_seen_at: x.last_seen_at, expires_at: x.expires_at, user_agent: x.user_agent, ip: x.ip, current: x.id === a.session.id }));
    return { items };
  }
  if (path === "/api/v1/projects") {
    const a = authenticate(ctx);
    return { items: membershipsOf(a.s, a.user.id) };
  }
  if ((m = /^\/api\/v1\/projects\/([^/]+)\/(members|keys|egress-hosts|audit-log)$/.exec(path))) {
    const a = authenticate(ctx);
    const [, pid, what] = m;
    requireRole(a, pid, "admin");
    const s = a.s;
    if (what === "members") {
      return {
        items: s.members
          .filter((x) => x.project_id === pid)
          .map((x) => ({ user: publicUser(s.users.find((u) => u.id === x.user_id)!), role: x.role, added_at: x.added_at })),
      };
    }
    if (what === "keys") return { items: s.keys.filter((k) => k.project_id === pid).map(({ project_id: _p, ...k }) => k) };
    if (what === "egress-hosts") return { items: s.egress.filter((h) => h.project_id === pid).map(({ project_id: _p, ...h }) => h) };
    const limit = Math.min(200, Math.max(1, Number(params.limit ?? 50)));
    const offset = Number(params.cursor ?? 0) || 0;
    const all = s.audit.filter((e) => e.project_id === pid && (!params.action || e.action === params.action));
    return {
      items: all.slice(offset, offset + limit).map(({ project_id: _p, ...e }) => e),
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    };
  }
  return undefined;
}

export function authSend(ctx: MockCtx, method: string, path: string, body: unknown): unknown {
  let m: RegExpExecArray | null;
  if (method === "POST" && path === "/api/v1/auth/login") return login(ctx, body);

  if (method === "POST" && path === "/api/v1/auth/logout") {
    const a = authenticate(ctx);
    a.session.token = `revoked_${a.session.id}`;
    a.session.expires_at = iso(0);
    return NO_CONTENT;
  }

  if (method === "POST" && path === "/api/v1/auth/password") {
    const a = authenticate(ctx);
    if (!isObject(body) || typeof body.current_password !== "string" || typeof body.new_password !== "string") bad("current_password and new_password are required");
    const { current_password, new_password } = body as { current_password: string; new_password: string };
    if (current_password !== a.user.password) throw new ApiError(403, JSON.stringify({ detail: "current password is incorrect" }));
    if (new_password.length < PASSWORD_MIN) bad(`password must be at least ${PASSWORD_MIN} characters`);
    if (new_password.length > PASSWORD_MAX) bad(`password must be at most ${PASSWORD_MAX} characters`);
    a.user.password = new_password;
    a.s.sessions = a.s.sessions.filter((x) => x.user_id !== a.user.id || x.id === a.session.id);
    return NO_CONTENT;
  }

  if ((m = /^\/api\/v1\/auth\/sessions\/([^/]+)$/.exec(path)) && method === "DELETE") {
    const a = authenticate(ctx);
    const i = a.s.sessions.findIndex((x) => x.id === m![1] && x.user_id === a.user.id);
    if (i < 0) throw new ApiError(404, JSON.stringify({ detail: "session not found" }));
    a.s.sessions.splice(i, 1);
    return NO_CONTENT;
  }

  if (method === "POST" && path === "/api/v1/projects") {
    const a = authenticate(ctx);
    const name = isObject(body) && typeof body.name === "string" ? body.name.trim() : "";
    if (!name || name.length > 200) bad("name must be 1-200 characters");
    if (a.s.projects.some((p) => p.name.toLowerCase() === name.toLowerCase())) throw new ApiError(409, JSON.stringify({ detail: "project name already exists" }));
    const project = { id: newId().slice(0, 32), name };
    a.s.projects.push(project);
    a.s.members.push({ user_id: a.user.id, project_id: project.id, role: "owner", added_at: iso(Date.now()) });
    record(a.s, project.id, a.user.id, "project.create", project.id, { name });
    return { project, role: "owner" };
  }

  if ((m = /^\/api\/v1\/projects\/([^/]+)\/(members|keys|egress-hosts)(?:\/([^/]+))?$/.exec(path))) {
    const a = authenticate(ctx);
    const [, pid, what, sub] = m;
    const myRole = requireRole(a, pid, "admin");
    const s = a.s;
    const actor = a.user.id;

    if (what === "members") {
      const ownerOnly = (...roles: (Role | null)[]) => {
        if (roles.some((r) => r && roleRank(r) >= roleRank("admin")) && myRole !== "owner") throw new ApiError(403, JSON.stringify({ detail: "requires role owner" }));
      };
      const otherOwner = (uid: string) => {
        if (!s.members.some((x) => x.project_id === pid && x.role === "owner" && x.user_id !== uid)) {
          throw new ApiError(409, JSON.stringify({ detail: "a project must keep at least one owner" }));
        }
      };
      const out = (row: MemberRow) => ({ user: publicUser(s.users.find((u) => u.id === row.user_id)!), role: row.role, added_at: row.added_at });
      if (method === "POST" && !sub) {
        if (!isObject(body) || typeof body.email !== "string" || !ROLES.includes(body.role as Role)) bad("email and a valid role are required");
        const { email, role } = body as { email: string; role: Role };
        ownerOnly(role);
        const user = s.users.find((u) => u.email === email.trim().toLowerCase());
        if (!user) throw new ApiError(404, JSON.stringify({ detail: "user not found" }));
        if (s.members.some((x) => x.project_id === pid && x.user_id === user.id)) throw new ApiError(409, JSON.stringify({ detail: "user is already a member" }));
        const row: MemberRow = { user_id: user.id, project_id: pid, role, added_at: iso(Date.now()) };
        s.members.push(row);
        record(s, pid, actor, "member.add", user.id, { role });
        return out(row);
      }
      const row = sub ? s.members.find((x) => x.project_id === pid && x.user_id === sub) : undefined;
      if (method === "PATCH" && sub) {
        if (!isObject(body) || !ROLES.includes(body.role as Role)) bad("a valid role is required");
        if (!row) throw new ApiError(404, JSON.stringify({ detail: "member not found" }));
        const next = (body as { role: Role }).role;
        ownerOnly(row.role, next);
        if (row.role === "owner" && next !== "owner") otherOwner(row.user_id);
        const from = row.role;
        row.role = next;
        record(s, pid, actor, "member.update", row.user_id, { from, to: next });
        return out(row);
      }
      if (method === "DELETE" && sub) {
        if (!row) throw new ApiError(404, JSON.stringify({ detail: "member not found" }));
        ownerOnly(row.role);
        if (row.role === "owner") otherOwner(row.user_id);
        s.members.splice(s.members.indexOf(row), 1);
        record(s, pid, actor, "member.remove", row.user_id, { role: row.role });
        return NO_CONTENT;
      }
    }

    if (what === "keys") {
      if (method === "POST" && !sub) {
        const name = isObject(body) && typeof body.name === "string" ? body.name.trim() : "";
        const scopes = isObject(body) && Array.isArray(body.scopes) ? body.scopes : [];
        if (!name || name.length > 200) bad("name must be 1-200 characters");
        if (scopes.length < 1 || !scopes.every((x) => KEY_SCOPES.includes(x as KeyScope))) bad("scopes must be a non-empty subset of ingest, read, write");
        const prefix = randomBytes(4).toString("hex");
        const secret = randomBytes(24).toString("base64url");
        const id = `k_${newId()}`;
        s.keys.push({ id, project_id: pid, prefix, name, scopes: [...new Set(scopes as KeyScope[])].sort(), created_at: iso(Date.now()), created_by: actor, last_used_at: null, revoked_at: null });
        record(s, pid, actor, "key.create", id, { prefix, name, scopes });
        return { id, prefix, api_key: `mx_${prefix}_${secret}` };
      }
      if (method === "DELETE" && sub) {
        const key = s.keys.find((k) => k.id === sub && k.project_id === pid);
        if (!key) throw new ApiError(404, JSON.stringify({ detail: "key not found" }));
        if (!key.revoked_at) {
          key.revoked_at = iso(Date.now());
          record(s, pid, actor, "key.revoke", key.id, { prefix: key.prefix });
        }
        return NO_CONTENT;
      }
    }

    if (what === "egress-hosts") {
      if (method === "POST" && !sub) {
        const raw = isObject(body) && typeof body.host === "string" ? body.host.trim().toLowerCase() : "";
        const err = validateEgressEntry(raw);
        if (err) bad(err);
        if (s.egress.some((h) => h.project_id === pid && h.host === raw)) throw new ApiError(409, JSON.stringify({ detail: "host already allowed" }));
        const row: EgressRow = { id: `h_${newId()}`, project_id: pid, host: raw, created_at: iso(Date.now()), created_by: actor };
        s.egress.push(row);
        record(s, pid, actor, "egress.host_add", raw);
        const { project_id: _p, ...out } = row;
        return out;
      }
      if (method === "DELETE" && sub) {
        const i = s.egress.findIndex((h) => h.id === sub && h.project_id === pid);
        if (i < 0) throw new ApiError(404, JSON.stringify({ detail: "egress host not found" }));
        record(s, pid, actor, "egress.host_remove", s.egress[i].host);
        s.egress.splice(i, 1);
        return NO_CONTENT;
      }
    }
  }
  return undefined;
}
