// Security headers applied to every response by next.config.mjs (see headers()).
// CSP limitation: Next 15 injects inline bootstrap scripts, so script-src needs 'unsafe-inline'
// (a nonce would require dynamic rendering of every page via middleware). The main XSS defence is
// output escaping (lib/escape-html.ts); the CSP still blocks off-origin scripts, framing, base-uri
// and cross-origin form posts/connections. Not applied in development (HMR needs eval/ws).
export type Header = { key: string; value: string };

export function contentSecurityPolicy(): string {
  return [
    "default-src 'self'",
    "img-src 'self' data: blob:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self' 'unsafe-inline'",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");
}

export function securityHeaders(isDev: boolean): Header[] {
  const base: Header[] = [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "same-origin" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  ];
  return isDev ? base : [{ key: "Content-Security-Policy", value: contentSecurityPolicy() }, ...base];
}
