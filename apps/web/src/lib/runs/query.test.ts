import { describe, expect, it } from "vitest";
import { compareHref, parseIds, runFilters, runListParams, runsHref } from "./query";

describe("parseIds", () => {
  it("trims, de-duplicates and keeps order", () => {
    expect(parseIds(" a, b ,a,,c ")).toEqual({ ids: ["a", "b", "c"], invalid: [], truncated: false });
  });
  it("accepts repeated params and reports invalid ids", () => {
    expect(parseIds(["a,b", "c", "bad id", "x/../y"])).toEqual({ ids: ["a", "b", "c"], invalid: ["bad id", "x/../y"], truncated: false });
  });
  it("caps at 20", () => {
    const raw = Array.from({ length: 25 }, (_, i) => `r${i}`).join(",");
    const out = parseIds(raw);
    expect(out.ids).toHaveLength(20);
    expect(out.truncated).toBe(true);
  });
  it("handles missing input", () => {
    expect(parseIds(undefined).ids).toEqual([]);
    expect(parseIds(null).ids).toEqual([]);
  });
});

describe("filters", () => {
  it("reads valid values and drops invalid ones", () => {
    const f = runFilters({ status: "running", sort: "name", q: "  llm ", tag: ["dpo", "x"], group: "", cursor: "abc" });
    expect(f).toEqual({ status: "running", group: null, tag: "dpo", q: "llm", sort: "name", cursor: "abc" });
    expect(runFilters({ status: "weird", sort: "drop table" })).toMatchObject({ status: null, sort: "-started_at" });
  });
  it("builds API params and hrefs", () => {
    const f = runFilters({ status: "failed", q: "a b", cursor: "c1" });
    expect(runListParams(f, 25)).toEqual({ status: "failed", group: undefined, tag: undefined, q: "a b", sort: "-started_at", limit: 25, cursor: "c1" });
    expect(runsHref(f, { tag: "x" })).toBe("/runs?status=failed&tag=x&q=a+b");
    expect(runsHref(f, { status: null, q: null })).toBe("/runs");
    expect(runsHref(f, { cursor: "n2" })).toBe("/runs?status=failed&q=a+b&cursor=n2");
    expect(compareHref(["a", "b c"])).toBe("/runs/compare?ids=a,b%20c");
  });
});
