/** Same-origin check for mutations: the Origin host must match the Host the request came to. */
export function sameOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return req.headers.get("sec-fetch-site") === "same-origin";
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }
  const host = req.headers.get("host") ?? new URL(req.url).host;
  return originHost === host;
}
