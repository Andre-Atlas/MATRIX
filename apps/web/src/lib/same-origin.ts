// Same-origin check for mutations (login, logout, switch-project, proxied writes).
import { cookiesSecure } from "./session-cookie";

/** `MATRIX_WEB_ORIGIN` normalised to `scheme://host[:port]`, or null when unset/invalid. */
export function configuredOrigin(): string | null {
  const raw = (process.env.MATRIX_WEB_ORIGIN ?? "").trim();
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:" ? u.origin : null;
  } catch {
    return null;
  }
}

/**
 * The origin this request was served under. With `MATRIX_WEB_ORIGIN` set, exactly that. Otherwise
 * the Host header plus the scheme of `request.url`, or of `X-Forwarded-Proto` only when the
 * deployment says a reverse proxy sits in front (`MATRIX_WEB_TRUST_FORWARDED_FOR=1`). With Secure
 * cookies (production) the app is HTTPS by definition, so the scheme is forced to https.
 */
export function expectedOrigin(req: Request): string | null {
  const fixed = configuredOrigin();
  if (fixed) return fixed;
  let url: URL;
  try {
    url = new URL(req.url);
  } catch {
    return null;
  }
  let scheme = url.protocol.replace(/:$/, "");
  if (process.env.MATRIX_WEB_TRUST_FORWARDED_FOR === "1") {
    const fwd = (req.headers.get("x-forwarded-proto") ?? "").split(",")[0].trim().toLowerCase();
    if (fwd === "http" || fwd === "https") scheme = fwd;
  }
  if (cookiesSecure()) scheme = "https";
  const host = req.headers.get("host") ?? url.host;
  try {
    return new URL(`${scheme}://${host}`).origin;
  } catch {
    return null;
  }
}

/** True when the request's Origin is exactly the origin the app is served under (scheme included). */
export function sameOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return req.headers.get("sec-fetch-site") === "same-origin";
  if (origin === "null") return false;
  let normalized: string;
  try {
    normalized = new URL(origin).origin;
  } catch {
    return false;
  }
  const expected = expectedOrigin(req);
  return expected !== null && normalized === expected;
}
