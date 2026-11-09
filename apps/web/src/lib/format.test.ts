import { describe, expect, it } from "vitest";
import {
  formatClock,
  formatCost,
  formatDuration,
  formatPayload,
  formatPercent,
  formatScore,
  formatTimestamp,
  formatTokens,
  formatValue,
  truncate,
} from "./format";

describe("format", () => {
  it("formats durations", () => {
    expect(formatDuration(0)).toBe("0 ms");
    expect(formatDuration(123)).toBe("123 ms");
    expect(formatDuration(1234)).toBe("1.23 s");
    expect(formatDuration(65_000)).toBe("1m 05s");
    expect(formatDuration(NaN)).toBe("—");
  });
  it("formats cost", () => {
    expect(formatCost(0.0012)).toBe("$0.0012");
    expect(formatCost(0)).toBe("$0.0000");
    expect(formatCost(123.456)).toBe("$123.46");
    expect(formatCost(0.00001785)).toBe("$0.000018");
  });
  it("formats tokens compactly", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(1234)).toBe("1.2k");
    expect(formatTokens(12_345)).toBe("12k");
    expect(formatTokens(3_400_000)).toBe("3.4M");
  });
  it("formats percentages", () => {
    expect(formatPercent(0.0425)).toBe("4.3%");
    expect(formatPercent(1, 0)).toBe("100%");
  });
  it("formats timestamps in UTC", () => {
    expect(formatTimestamp("2026-11-09T10:01:02.345Z")).toBe("2026-11-09 10:01:02");
    expect(formatClock("2026-11-09T10:01:02.345Z")).toBe("10:01:02.345");
    expect(formatTimestamp("not a date")).toBe("not a date");
  });
  it("formats scores", () => {
    expect(formatScore(1)).toBe("1");
    expect(formatScore(0.876)).toBe("0.88");
  });
  it("truncates", () => {
    expect(truncate("hello", 10)).toBe("hello");
    expect(truncate("hello world", 6)).toBe("hello…");
  });
  it("pretty-prints JSON payloads", () => {
    expect(formatPayload('{"a":1}', "application/json")).toEqual({ text: '{\n  "a": 1\n}', json: true });
    expect(formatPayload("[1, 2]")).toEqual({ text: "[\n  1,\n  2\n]", json: true });
    expect(formatPayload('{"a":1}', "text/plain")).toEqual({ text: '{"a":1}', json: false });
    expect(formatPayload("{broken", "application/json")).toEqual({ text: "{broken", json: false });
    expect(formatPayload("  plain\n  text")).toEqual({ text: "  plain\n  text", json: false });
  });
  it("formats attribute values", () => {
    expect(formatValue(null)).toBe("null");
    expect(formatValue("x")).toBe("x");
    expect(formatValue(3)).toBe("3");
    expect(formatValue({ a: [1] })).toBe('{"a":[1]}');
  });
});
