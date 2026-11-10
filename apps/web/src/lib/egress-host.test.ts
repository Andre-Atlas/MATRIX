import { describe, expect, it } from "vitest";
import { validateEgressEntry } from "./egress-host";
import { validEmail, validateNewPassword, retryText } from "./validation";

describe("validateEgressEntry", () => {
  it.each(["example.com", "api.example.com", "*.example.com", "  API.Example.COM ", "xn--bcher-kva.example"])("accepts %j", (h) =>
    expect(validateEgressEntry(h)).toBeNull(),
  );

  it.each([
    ["", /host name/],
    ["*", /wildcard/],
    ["*.", /fully qualified|Invalid|host/],
    ["*example.com", /wildcard|Invalid/],
    ["a.*.example.com", /wildcard/],
    ["**.example.com", /wildcard/],
    ["127.0.0.1", /IP/],
    ["10.0.0.1", /IP/],
    ["[::1]", /IP/],
    ["::1", /IP/],
    ["localhost", /localhost|fully/],
    ["foo.localhost", /localhost/],
    ["example", /fully qualified/],
    ["https://example.com", /no scheme|Use only/],
    ["example.com:443", /Use only/],
    ["example.com/path", /Use only/],
    ["user@example.com", /Use only/],
    ["co.uk", /suffix/],
    ["*.github.io", /suffix/],
    ["a_b.example.com", /Invalid/],
    ["-a.example.com", /Invalid/],
  ])("rejects %j", (h, msg) => expect(validateEgressEntry(h)).toMatch(msg));
});

describe("validateNewPassword", () => {
  it("requires 12+ characters, a different value and a matching confirmation", () => {
    expect(validateNewPassword("old-password-1", "short", "short")).toMatch(/at least 12/);
    expect(validateNewPassword("", "long-enough-password", "long-enough-password")).toMatch(/current/);
    expect(validateNewPassword("same-password-12", "same-password-12", "same-password-12")).toMatch(/differ/);
    expect(validateNewPassword("old-password-1", "long-enough-password", "other-long-password")).toMatch(/confirmation/);
    expect(validateNewPassword("old-password-1", "x".repeat(257), "x".repeat(257))).toMatch(/at most 256/);
    expect(validateNewPassword("old-password-1", "a-new-password-12", "a-new-password-12")).toBeNull();
    expect(validateNewPassword("old", "exactly12chr!", "exactly12chr!")).toBeNull();
  });

  it("checks emails and formats the retry delay", () => {
    expect(validEmail("a@b.co")).toBe(true);
    expect(validEmail("a b@c.d")).toBe(false);
    expect(retryText(30)).toBe("30 s");
    expect(retryText(0)).toBe("1 s");
    expect(retryText(300)).toBe("5 min");
  });
});
