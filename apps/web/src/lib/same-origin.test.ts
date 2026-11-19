// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { sameOrigin } from "./same-origin";

type Case = {
  name: string;
  url: string;
  headers: Record<string, string>;
  env?: Record<string, string>;
  ok: boolean;
};

const cases: Case[] = [
  { name: "same scheme and host", url: "http://m.local:3000/x", headers: { host: "m.local:3000", origin: "http://m.local:3000" }, ok: true },
  { name: "other host", url: "http://m.local:3000/x", headers: { host: "m.local:3000", origin: "http://evil.example" }, ok: false },
  { name: "scheme differs from request.url", url: "https://m.local/x", headers: { host: "m.local", origin: "http://m.local" }, ok: false },
  { name: "opaque null origin", url: "http://m.local/x", headers: { host: "m.local", origin: "null" }, ok: false },
  { name: "garbage origin", url: "http://m.local/x", headers: { host: "m.local", origin: "::::" }, ok: false },
  { name: "no Origin, same-origin fetch metadata", url: "http://m.local/x", headers: { host: "m.local", "sec-fetch-site": "same-origin" }, ok: true },
  { name: "no Origin, cross-site", url: "http://m.local/x", headers: { host: "m.local", "sec-fetch-site": "cross-site" }, ok: false },
  {
    name: "X-Forwarded-Proto ignored without the trust flag",
    url: "http://m.local/x",
    headers: { host: "m.local", origin: "https://m.local", "x-forwarded-proto": "https" },
    ok: false,
  },
  {
    name: "X-Forwarded-Proto used with the trust flag",
    url: "http://m.local/x",
    headers: { host: "m.local", origin: "https://m.local", "x-forwarded-proto": "https" },
    env: { MATRIX_WEB_TRUST_FORWARDED_FOR: "1" },
    ok: true,
  },
  {
    name: "production: http Origin against an https-served host is refused",
    url: "http://m.local/x",
    headers: { host: "m.local", origin: "http://m.local" },
    env: { NODE_ENV: "production" },
    ok: false,
  },
  {
    name: "production: https Origin accepted",
    url: "http://m.local/x",
    headers: { host: "m.local", origin: "https://m.local" },
    env: { NODE_ENV: "production" },
    ok: true,
  },
  {
    name: "MATRIX_WEB_ORIGIN: exact match",
    url: "http://internal:3000/x",
    headers: { host: "internal:3000", origin: "https://matrix.example.com" },
    env: { MATRIX_WEB_ORIGIN: "https://matrix.example.com" },
    ok: true,
  },
  {
    name: "MATRIX_WEB_ORIGIN: the Host header no longer decides",
    url: "http://evil.example/x",
    headers: { host: "evil.example", origin: "http://evil.example" },
    env: { MATRIX_WEB_ORIGIN: "https://matrix.example.com" },
    ok: false,
  },
  {
    name: "MATRIX_WEB_ORIGIN: other port",
    url: "http://internal:3000/x",
    headers: { host: "internal:3000", origin: "https://matrix.example.com:8443" },
    env: { MATRIX_WEB_ORIGIN: "https://matrix.example.com" },
    ok: false,
  },
  {
    name: "MATRIX_WEB_ORIGIN: scheme differs",
    url: "http://internal:3000/x",
    headers: { host: "internal:3000", origin: "http://matrix.example.com" },
    env: { MATRIX_WEB_ORIGIN: "https://matrix.example.com" },
    ok: false,
  },
];

describe("sameOrigin", () => {
  afterEach(() => vi.unstubAllEnvs());

  it.each(cases)("$name", ({ url, headers, env, ok }) => {
    vi.stubEnv("MATRIX_WEB_ORIGIN", "");
    vi.stubEnv("MATRIX_WEB_TRUST_FORWARDED_FOR", "");
    vi.stubEnv("MATRIX_INSECURE_COOKIES", "");
    for (const [k, v] of Object.entries(env ?? {})) vi.stubEnv(k, v);
    expect(sameOrigin(new Request(url, { method: "POST", headers }))).toBe(ok);
  });
});
