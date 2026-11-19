// Pure helpers for the G0 session cookies. No Next imports, so the middleware (edge), route
// handlers and Server Components share them. The session token only ever lives in the HttpOnly
// session cookie (`__Host-matrix_session` in production, `matrix_session` in dev): it is read server-side and never handed to client components.

// Base names. In production (Secure cookies) the real names carry the `__Host-` prefix, which the
// browser only accepts with Secure, Path=/ and no Domain: a sibling subdomain or a plain-HTTP page
// cannot plant or overwrite them. Always go through the *CookieName() helpers below.
export const SESSION_COOKIE = "matrix_session";
export const PROJECT_COOKIE = "matrix_project";
/** Persistent (survives logout) `<user id>~<project id>`: the project this browser's user last used. */
export const LAST_PROJECT_COOKIE = "matrix_last_project";
export const HOST_PREFIX = "__Host-";

/** `mxs_<token_urlsafe(32)>` from the backend; the shape check also keeps header injection out. */
export const TOKEN_RE = /^mxs_[A-Za-z0-9_-]{8,256}$/;
export const PROJECT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const USER_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

export type Session = { token: string; projectId: string | null };

export function cookieName(base: string): string {
  return cookiesSecure() ? `${HOST_PREFIX}${base}` : base;
}
export const sessionCookieName = () => cookieName(SESSION_COOKIE);
export const projectCookieName = () => cookieName(PROJECT_COOKIE);
export const lastProjectCookieName = () => cookieName(LAST_PROJECT_COOKIE);

/** Every `name=value` pair in order, duplicates included. */
export function cookiePairs(header: string | null | undefined): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  if (!header) return out;
  for (const part of header.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const name = part.slice(0, i).trim();
    if (name) out.push([name, part.slice(i + 1).trim()]);
  }
  return out;
}

/**
 * Cookie jar. A name sent more than once is ambiguous (a cookie planted with another Path or by a
 * sibling host can shadow ours, and the browser's order is not a security boundary): it is LEFT OUT.
 */
export function parseCookies(header: string | null | undefined): Record<string, string> {
  const counts = new Map<string, number>();
  const pairs = cookiePairs(header);
  for (const [name] of pairs) counts.set(name, (counts.get(name) ?? 0) + 1);
  const out: Record<string, string> = {};
  for (const [name, value] of pairs) if (counts.get(name) === 1) out[name] = value;
  return out;
}

/** True when a session/project cookie name appears twice: the session is refused and cleared. */
export function sessionCookieConflict(header: string | null | undefined): boolean {
  const names = [sessionCookieName(), projectCookieName()];
  const seen = new Map<string, number>();
  for (const [name] of cookiePairs(header)) if (names.includes(name)) seen.set(name, (seen.get(name) ?? 0) + 1);
  return [...seen.values()].some((n) => n > 1);
}

export function sessionFromCookieHeader(header: string | null | undefined): Session | null {
  if (sessionCookieConflict(header)) return null;
  const jar = parseCookies(header);
  // Only the expected (prefixed in production) name counts; a bare `matrix_session` is ignored there.
  const token = jar[sessionCookieName()];
  if (!token || !TOKEN_RE.test(token)) return null;
  const project = jar[projectCookieName()];
  return { token, projectId: project && PROJECT_ID_RE.test(project) ? project : null };
}

/** The last project of `userId` on this browser, if the persistent cookie belongs to that user. */
export function lastProjectFor(header: string | null | undefined, userId: unknown): string | null {
  if (typeof userId !== "string" || !USER_ID_RE.test(userId)) return null;
  const raw = parseCookies(header)[lastProjectCookieName()];
  if (!raw) return null;
  const i = raw.indexOf("~");
  if (i < 0) return null;
  const [uid, pid] = [raw.slice(0, i), raw.slice(i + 1)];
  return uid === userId && PROJECT_ID_RE.test(pid) ? pid : null;
}

export function setLastProject(headers: Headers, userId: unknown, projectId: string) {
  if (typeof userId !== "string" || !USER_ID_RE.test(userId) || !PROJECT_ID_RE.test(projectId)) return;
  headers.append("set-cookie", serializeCookie(lastProjectCookieName(), `${userId}~${projectId}`, 180 * 86_400));
}

export function cookiesSecure(): boolean {
  return process.env.NODE_ENV === "production" && process.env.MATRIX_INSECURE_COOKIES !== "1";
}

/**
 * Set-Cookie value. HttpOnly always; SameSite=Lax (not Strict: a Strict cookie is not sent on the
 * first top-level navigation from another site, so a link into the app would look logged out;
 * mutations are protected by the same-origin check instead); Secure outside development.
 * `maxAge: 0` clears the cookie.
 */
export function serializeCookie(name: string, value: string, maxAge: number): string {
  const parts = [`${name}=${value}`, "Path=/", "HttpOnly", "SameSite=Lax"];
  if (cookiesSecure()) parts.push("Secure");
  if (maxAge <= 0) parts.push("Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT");
  else parts.push(`Max-Age=${Math.floor(maxAge)}`);
  return parts.join("; ");
}

/** Seconds from now to `expiresAt`, clamped to [60 s, 31 d]; null when it is not a valid date. */
export function lifetimeSeconds(expiresAt: unknown, now = Date.now()): number | null {
  if (typeof expiresAt !== "string") return null;
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return null;
  return Math.min(31 * 86_400, Math.max(60, Math.floor((t - now) / 1000)));
}

export function setSessionCookies(headers: Headers, token: string, projectId: string | null, maxAge: number) {
  headers.append("set-cookie", serializeCookie(sessionCookieName(), token, maxAge));
  if (projectId) headers.append("set-cookie", serializeCookie(projectCookieName(), projectId, maxAge));
  else headers.append("set-cookie", serializeCookie(projectCookieName(), "", 0));
}

/** Clears the session and project cookies (and, in production, the legacy unprefixed names). */
export function clearSessionCookies(headers: Headers) {
  const names = new Set([sessionCookieName(), projectCookieName(), SESSION_COOKIE, PROJECT_COOKIE]);
  for (const name of names) headers.append("set-cookie", serializeCookie(name, "", 0));
}

/**
 * Post-login destination. Only a same-site relative path is accepted: starts with a single `/`, no
 * `//` or backslash (protocol-relative or browser-normalised to `//`), no scheme, no control
 * characters. Never loops back to /login or into /api. Anything else becomes "/".
 */
export function safeNext(raw: unknown): string {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 2000) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/";
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return "/";
  let url: URL;
  try {
    url = new URL(raw, "http://matrix.invalid");
  } catch {
    return "/";
  }
  if (url.origin !== "http://matrix.invalid" || url.pathname.startsWith("//")) return "/";
  if (url.pathname === "/login" || url.pathname === "/api" || url.pathname.startsWith("/api/")) return "/";
  return url.pathname + url.search + url.hash;
}
