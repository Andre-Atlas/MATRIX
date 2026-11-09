import { describe, expect, it } from "vitest";
import { formatCost, formatDuration } from "./format";

describe("format", () => {
  it("formats durations", () => {
    expect(formatDuration(123)).toBe("123 ms");
    expect(formatDuration(1234)).toBe("1.23 s");
  });
  it("formats cost", () => {
    expect(formatCost(0.0012)).toBe("$0.0012");
  });
});
