// Headers for calls from the Next server to the MATRIX API.
import { PROJECT_ID_RE, TOKEN_RE, type Session } from "./session-cookie";

export const apiBase = () => (process.env.MATRIX_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

const IPV4 = /^(?:\d{1,3}\.){3}\d{1,3}$/;
const IPV6 = /^[0-9a-fA-F:.]{2,45}$/;

/**
 * The client address, only when the deployment says a reverse proxy sits in front of Next
 * (`MATRIX_WEB_TRUST_FORWARDED_FOR=1`): the RIGHTMOST X-Forwarded-For entry, i.e. the one that proxy
 * appended for the peer it saw; anything to its left is client-controlled and ignored.
 *
 * Without that flag the result is null: Next only fills X-Forwarded-For when the request has none
 * (`??=` in next/dist/server/base-server.js), so a browser that talks to Next directly could put
 * any address there and dodge the backend's per-IP login throttle. The backend then falls back to
 * the socket peer, which is the Next server itself (coarser, but not spoofable).
 */
export function clientIp(req: Request): string | null {
  if (process.env.MATRIX_WEB_TRUST_FORWARDED_FOR !== "1") return null;
  const raw = req.headers.get("x-forwarded-for");
  if (!raw) return null;
  const last = raw.split(",").at(-1)?.trim() ?? "";
  if (IPV4.test(last)) return last.split(".").every((n) => Number(n) <= 255) ? last : null;
  return last.includes(":") && IPV6.test(last) ? last : null;
}

/** Name of the header the backend trusts for the client IP (`MATRIX_TRUSTED_PROXY_HEADER`), if any. */
export function trustedProxyHeader(): string | null {
  const name = (process.env.MATRIX_TRUSTED_PROXY_HEADER ?? "").trim().toLowerCase();
  return /^[a-z0-9-]{1,64}$/.test(name) ? name : null;
}

/**
 * Upstream request headers, built from scratch: nothing the browser sent is forwarded (no cookie,
 * authorization, project or IP header). The trusted-proxy header is always OVERWRITTEN with the IP
 * the Next server sees, never passed through.
 */
export function upstreamHeaders(
  req: Request | null,
  session: Session | null,
  extra: Record<string, string> = {},
): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (session && TOKEN_RE.test(session.token)) {
    h.authorization = `Bearer ${session.token}`;
    if (session.projectId && PROJECT_ID_RE.test(session.projectId)) h["x-matrix-project"] = session.projectId;
  }
  const name = trustedProxyHeader();
  if (name && req) {
    const ip = clientIp(req);
    if (ip) h[name] = ip;
  }
  return h;
}
