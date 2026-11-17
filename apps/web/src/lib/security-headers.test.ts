import { describe, expect, it } from "vitest";
import { securityHeaders } from "./security-headers";

const get = (h: { key: string; value: string }[], k: string) => h.find((x) => x.key === k)?.value;

describe("securityHeaders", () => {
  it("production: restrictive CSP plus hardening headers", () => {
    const h = securityHeaders(false);
    const csp = get(h, "Content-Security-Policy")!;
    for (const d of ["default-src 'self'", "connect-src 'self'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'", "object-src 'none'"]) expect(csp).toContain(d);
    expect(csp).not.toContain("unsafe-eval");
    expect(get(h, "X-Content-Type-Options")).toBe("nosniff");
    expect(get(h, "Referrer-Policy")).toBe("same-origin");
    expect(get(h, "X-Frame-Options")).toBe("DENY");
    expect(get(h, "Permissions-Policy")).toContain("camera=()");
  });
  it("development: no CSP (HMR)", () => {
    expect(get(securityHeaders(true), "Content-Security-Policy")).toBeUndefined();
  });
});
