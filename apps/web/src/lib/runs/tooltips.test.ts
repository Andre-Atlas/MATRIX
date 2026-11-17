import { describe, expect, it } from "vitest";
import { confusionTooltip, importanceTooltip, scatterTooltip } from "./tooltips";

const EVIL = "<img src=x onerror=alert(1)>";

describe("tooltips", () => {
  it("confusion: escapes both labels", () => {
    const h = confusionTooltip(EVIL, '"><script>x</script>', 0.5, true);
    expect(h).not.toContain("<img");
    expect(h).not.toContain("<script");
    expect(h).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(h).toContain("<b>");
    expect(h).toContain("50.0% of row");
  });
  it("confusion: raw count", () => expect(confusionTooltip("a", "b", 7, false)).toBe("true <b>a</b> → predicted <b>b</b><br/>7"));
  it("importance: escapes feature name", () => {
    const h = importanceTooltip(EVIL, 0.25, 0.01);
    expect(h).not.toContain("<img");
    expect(h).toContain("&lt;img");
    expect(h).toContain("±");
  });
  it("importance: no std", () => expect(importanceTooltip("f", 1)).not.toContain("±"));
  it("scatter: numbers only", () => expect(scatterTooltip(1, 2)).toMatch(/^true .*<br\/>pred /));
});
