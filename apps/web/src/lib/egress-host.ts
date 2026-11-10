// Client-side pre-check of an egress allowlist entry (contract g0 section 1; the server is the
// authority and applies the same rules plus IDNA/punycode and a public-suffix list).

const LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const SUFFIXES = new Set(["co.uk", "com.br", "com.au", "co.jp", "github.io", "herokuapp.com", "vercel.app", "netlify.app", "amazonaws.com"]);

/** Error message for an invalid entry, or null when it looks acceptable (`example.com`, `*.example.com`). */
export function validateEgressEntry(raw: string): string | null {
  const value = raw.trim().toLowerCase();
  if (!value) return "Enter a host name.";
  if (/^\[?[0-9a-f:.]*:[0-9a-f:.]*\]?$/.test(value)) return "IP addresses are not allowed.";
  if (/[\s/:@?#\\]/.test(value)) return "Use only a host name: no scheme, port, path or credentials.";
  const wildcard = value.startsWith("*.");
  const rest = wildcard ? value.slice(2) : value;
  if (rest.includes("*")) return "A wildcard is only allowed as a leading '*.' (for example *.example.com).";
  if (/^[\d.]+$/.test(rest) || rest.includes("[") || /^[0-9a-f:]+$/.test(rest) && rest.includes(":")) {
    return "IP addresses are not allowed.";
  }
  const host = rest.replace(/\.$/, "");
  if (host === "localhost" || host.endsWith(".localhost")) return "localhost is not allowed.";
  if (host.length > 253) return "Host name is too long.";
  const labels = host.split(".");
  if (labels.length < 2) return "Use a fully qualified domain name (for example api.example.com).";
  if (!labels.every((l) => LABEL.test(l) || l.startsWith("xn--"))) return "Invalid host name.";
  if (/^\d+$/.test(labels.at(-1)!)) return "Numeric host names are not allowed.";
  if (SUFFIXES.has(host)) return "Public suffixes are not allowed.";
  return null;
}
