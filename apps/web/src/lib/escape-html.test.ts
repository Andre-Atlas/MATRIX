import { describe, expect, it } from "vitest";
import { escapeHtml } from "./escape-html";

describe("escapeHtml", () => {
  it.each([
    ["<img src=x onerror=alert(1)>", "&lt;img src=x onerror=alert(1)&gt;"],
    ['"><script>alert(1)</script>', "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"],
    ['" onmouseover="x', "&quot; onmouseover=&quot;x"],
    ["' onfocus='x", "&#39; onfocus=&#39;x"],
    ["javascript:alert(1)", "javascript:alert(1)"], // no markup chars: inert as text
    ["çãõ 日本語 😀", "çãõ 日本語 😀"],
  ])("escapes %s", (input, out) => expect(escapeHtml(input)).toBe(out));

  it("escapes & once per call (callers escape raw data, never twice)", () => {
    expect(escapeHtml("a & b")).toBe("a &amp; b");
    expect(escapeHtml("&lt;")).toBe("&amp;lt;");
  });
  it("never leaves raw angle brackets or quotes", () => {
    expect(escapeHtml(`<>"'&`)).not.toMatch(/[<>"']/);
  });
  it("handles non-strings", () => {
    expect(escapeHtml(null)).toBe("");
    expect(escapeHtml(3)).toBe("3");
  });
});
