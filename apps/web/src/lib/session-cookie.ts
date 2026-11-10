// Pure helpers for the G0 session cookies. No Next imports, so the middleware (edge), route
// handlers and Server Components share them. The session token only ever lives in the HttpOnly
// `matrix_session` cookie: it is read server-side and never handed to client components.

export const SESSION_COOKIE = "matrix_session";
export const PROJECT_COOKIE = "matrix_project";

/** `mxs_<token_urlsafe(32)>` from the backend; the shape check also keeps header injection out. */
export const TOKEN_RE = /^mxs_[A-Za-z0-9_-]{8,256}$/;
export const PROJECT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

export type Session = { token: string; projectId: string | null };

export function parseCookies(header: string | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!header) return out;
  for (const part of header.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const name = part.slice(0, i).trim();
    if (name && !(name in out)) out[name] = part.slice(i + 1).trim();
  }
  return out;
}

export function sessionFromCookieHeader(header: string | null | undefined): Session | null {
  const jar = parseCookies(header);
  const token = jar[SESSION_COOKIE];
  if (!token || !TOKEN_RE.test(token)) return null;
  const project = jar[PROJECT_COOKIE];
  return { token, projectId: project && PROJECT_ID_RE.test(project) ? project : null };
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
  headers.append("set-cookie", serializeCookie(SESSION_COOKIE, token, maxAge));
  if (projectId) headers.append("set-cookie", serializeCookie(PROJECT_COOKIE, projectId, maxAge));
  else headers.append("set-cookie", serializeCookie(PROJECT_COOKIE, "", 0));
}

export function clearSessionCookies(headers: Headers) {
  headers.append("set-cookie", serializeCookie(SESSION_COOKIE, "", 0));
  headers.append("set-cookie", serializeCookie(PROJECT_COOKIE, "", 0));
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
